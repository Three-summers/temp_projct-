from __future__ import annotations

import argparse
import logging
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass

from .protocol import (
    ItMessage,
    MESSAGE_CLEAN_START,
    MESSAGE_ERROR_COMMAND,
    MESSAGE_MOVE_IN,
    MESSAGE_MOVE_OUT,
    build_error_command_frame,
    build_start_clean_command_frame,
    extract_it_messages,
    format_frame_bytes,
)


LINE_ENDINGS = {
    "none": b"",
    "cr": b"\r",
    "lf": b"\n",
    "crlf": b"\r\n",
}

AUTO_RESPONSE_MODES = ("none", "start-clean", "error")


def parse_error_code(raw_value: str) -> int:
    normalized = raw_value.strip()
    if not normalized:
        raise ValueError("错误码不能为空。")

    if normalized.lower().startswith("0x"):
        value = int(normalized, 16)
    elif normalized == "33":
        value = 0x33
    else:
        value = int(normalized, 10)

    if not 0 <= value <= 0xFF:
        raise ValueError("错误码必须在 0x00 到 0xFF 之间。")
    return value


def encode_barcode_payload(barcode: str, line_ending: str) -> bytes:
    suffix = LINE_ENDINGS[line_ending]
    return barcode.encode("ascii", errors="ignore") + suffix


def describe_it_message(message: ItMessage) -> str:
    if message.code == MESSAGE_MOVE_IN:
        return (
            f"Move In rack_id={message.rack_id} "
            f"mask_id={message.mask_id or ''}"
        )
    if message.code == MESSAGE_CLEAN_START:
        return f"Clean Start rack_id={message.rack_id}"
    if message.code == MESSAGE_MOVE_OUT:
        return f"Move Out rack_id={message.rack_id}"
    if message.code == MESSAGE_ERROR_COMMAND:
        return (
            f"Error Command rack_id={message.rack_id} "
            f"error_code=0x{(message.error_code or 0):02X}"
        )
    return f"Unknown code=0x{message.code:02X} rack_id={message.rack_id}"


def build_auto_response_frame(
    message: ItMessage,
    rack_id: str,
    auto_response: str,
    error_code: int,
) -> bytes | None:
    if message.code != MESSAGE_MOVE_IN or auto_response == "none":
        return None

    if auto_response == "start-clean":
        return build_start_clean_command_frame(rack_id)

    if auto_response == "error":
        return build_error_command_frame(rack_id, error_code)

    raise ValueError(f"不支持的自动应答模式: {auto_response}")


@dataclass(slots=True)
class LabOptions:
    it_device: str
    scanner_device: str
    rack_id: str
    baudrate: int
    timeout: float
    auto_response: str
    response_delay: float
    error_code: int
    barcodes: list[str]
    barcode_interval: float
    line_ending: str
    interactive: bool
    log_level: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="虚拟串口联调工具，模拟 IT 端和扫码枪端。",
    )
    parser.add_argument("--it-device", required=True, help="IT 端伪串口对端路径。")
    parser.add_argument(
        "--scanner-device",
        required=True,
        help="扫码枪端伪串口对端路径。",
    )
    parser.add_argument("--rack-id", default="RPTEST", help="IT 联调使用的 Rack ID。")
    parser.add_argument("--baudrate", type=int, default=9600, help="串口波特率。")
    parser.add_argument("--timeout", type=float, default=0.2, help="串口读取超时。")
    parser.add_argument(
        "--auto-response",
        choices=AUTO_RESPONSE_MODES,
        default="start-clean",
        help="收到 Move In 之后的自动应答模式。",
    )
    parser.add_argument(
        "--response-delay",
        type=float,
        default=0.0,
        help="自动应答前的延迟秒数。",
    )
    parser.add_argument(
        "--error-code",
        type=parse_error_code,
        default="0x33",
        help="自动 error 模式或手动 error 命令使用的错误码。",
    )
    parser.add_argument(
        "--barcode",
        action="append",
        default=[],
        help="启动后立即发送的条码，可重复指定。",
    )
    parser.add_argument(
        "--barcode-interval",
        type=float,
        default=0.3,
        help="多条启动条码之间的间隔秒数。",
    )
    parser.add_argument(
        "--line-ending",
        choices=tuple(LINE_ENDINGS),
        default="crlf",
        help="扫码枪发送条码时附加的行尾。",
    )
    parser.add_argument(
        "--interactive",
        action=argparse.BooleanOptionalAction,
        default=sys.stdin.isatty(),
        help="是否开启交互命令行，默认跟随当前终端。",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="日志级别。",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> LabOptions:
    namespace = build_parser().parse_args(argv)
    return LabOptions(
        it_device=namespace.it_device,
        scanner_device=namespace.scanner_device,
        rack_id=namespace.rack_id,
        baudrate=namespace.baudrate,
        timeout=namespace.timeout,
        auto_response=namespace.auto_response,
        response_delay=namespace.response_delay,
        error_code=namespace.error_code,
        barcodes=list(namespace.barcode),
        barcode_interval=namespace.barcode_interval,
        line_ending=namespace.line_ending,
        interactive=bool(namespace.interactive),
        log_level=namespace.log_level,
    )


class MockSerialLab:
    """连接虚拟串口对端，模拟 IT 端和扫码枪端。"""

    def __init__(
        self,
        options: LabOptions,
        logger: logging.Logger | None = None,
    ) -> None:
        self._options = options
        self._logger = logger or logging.getLogger(__name__)
        self._stop_event = threading.Event()
        self._it_buffer = bytearray()
        self._it_serial: object | None = None
        self._scanner_serial: object | None = None
        self._reader_thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._last_cycle_key: tuple[str, str] | None = None

    def start(self) -> None:
        self._it_serial = self._open_serial(self._options.it_device)
        self._scanner_serial = self._open_serial(self._options.scanner_device)
        self._reader_thread = threading.Thread(
            target=self._read_it_loop,
            name="mock-it-reader",
            daemon=True,
        )
        self._reader_thread.start()
        self._logger.info(
            "Mock 串口已连接: IT=%s SCAN=%s auto_response=%s rack_id=%s",
            self._options.it_device,
            self._options.scanner_device,
            self._options.auto_response,
            self._options.rack_id,
        )

    def stop(self) -> None:
        self._stop_event.set()

        it_serial = self._it_serial
        scanner_serial = self._scanner_serial
        self._it_serial = None
        self._scanner_serial = None

        if it_serial is not None and hasattr(it_serial, "close"):
            it_serial.close()
        if scanner_serial is not None and hasattr(scanner_serial, "close"):
            scanner_serial.close()

        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
            self._reader_thread = None

    def send_barcode(self, barcode: str) -> None:
        with self._state_lock:
            self._last_cycle_key = None

        payload = encode_barcode_payload(barcode, self._options.line_ending)
        scanner_serial = self._require_scanner_serial()
        scanner_serial.write(payload)
        if hasattr(scanner_serial, "flush"):
            scanner_serial.flush()
        self._logger.info(
            "SCAN -> APP: barcode=%s payload=%s",
            barcode,
            format_frame_bytes(payload),
        )

    def send_start_clean(self) -> None:
        self._send_it_frame(
            build_start_clean_command_frame(self._options.rack_id),
            "手动 Start Clean",
        )

    def send_error(self, error_code: int) -> None:
        self._send_it_frame(
            build_error_command_frame(self._options.rack_id, error_code),
            f"手动 Error 0x{error_code:02X}",
        )

    def run(self) -> None:
        self.start()
        try:
            self._send_initial_barcodes()
            if self._options.interactive:
                self._run_repl()
            else:
                while not self._stop_event.wait(0.1):
                    continue
        except KeyboardInterrupt:
            self._logger.info("收到中断，准备退出。")
        finally:
            self.stop()

    def _send_initial_barcodes(self) -> None:
        for index, barcode in enumerate(self._options.barcodes):
            if index and self._stop_event.wait(self._options.barcode_interval):
                return
            self.send_barcode(barcode)

    def _run_repl(self) -> None:
        self._print_help()
        while not self._stop_event.is_set():
            try:
                raw_command = input("mock-serial> ").strip()
            except EOFError:
                self._logger.info("检测到输入结束，准备退出。")
                return

            if not raw_command:
                continue
            if raw_command in {"quit", "exit"}:
                return
            if raw_command == "help":
                self._print_help()
                continue
            if raw_command == "status":
                self._logger.info(
                    "当前模式: auto_response=%s error_code=0x%02X rack_id=%s",
                    self._options.auto_response,
                    self._options.error_code,
                    self._options.rack_id,
                )
                continue

            command, _, argument = raw_command.partition(" ")
            if command == "barcode":
                if not argument:
                    self._logger.warning("barcode 命令需要传入条码文本。")
                    continue
                self.send_barcode(argument)
                continue
            if command == "start-clean":
                self.send_start_clean()
                continue
            if command == "error":
                try:
                    error_code = (
                        parse_error_code(argument)
                        if argument
                        else self._options.error_code
                    )
                except ValueError as exc:
                    self._logger.warning("%s", exc)
                    continue
                self.send_error(error_code)
                continue
            if command == "mode":
                if argument not in AUTO_RESPONSE_MODES:
                    self._logger.warning(
                        "mode 命令只支持: %s",
                        ", ".join(AUTO_RESPONSE_MODES),
                    )
                    continue
                self._options.auto_response = argument
                self._logger.info("自动应答模式已切换为 %s", argument)
                continue

            self._logger.warning("未知命令: %s", raw_command)

    def _read_it_loop(self) -> None:
        while not self._stop_event.is_set():
            it_serial = self._it_serial
            if it_serial is None:
                return

            try:
                payload = it_serial.read(256)
            except Exception:
                if self._stop_event.is_set():
                    return
                self._logger.exception("读取 IT 串口失败。")
                continue

            if not payload:
                continue

            self._logger.info("APP -> IT RAW: %s", format_frame_bytes(payload))
            self._it_buffer.extend(payload)
            try:
                messages = extract_it_messages(self._it_buffer)
            except ValueError:
                self._logger.exception("解析 APP 发出的 IT 报文失败，清空缓冲。")
                self._it_buffer.clear()
                continue

            for message in messages:
                self._handle_it_message(message)

    def _handle_it_message(self, message: ItMessage) -> None:
        self._logger.info("APP -> IT: %s", describe_it_message(message))

        if message.code == MESSAGE_MOVE_OUT:
            with self._state_lock:
                self._last_cycle_key = None
            return

        if message.code != MESSAGE_MOVE_IN:
            return

        if message.rack_id != self._options.rack_id:
            self._logger.warning(
                "收到的 Move In rack_id=%s 与 mock 配置 rack_id=%s 不一致。",
                message.rack_id,
                self._options.rack_id,
            )

        cycle_key = (message.rack_id, message.mask_id or "")
        with self._state_lock:
            if cycle_key == self._last_cycle_key:
                self._logger.debug("检测到重复 Move In，忽略重复自动应答。")
                return
            self._last_cycle_key = cycle_key

        frame = build_auto_response_frame(
            message,
            self._options.rack_id,
            self._options.auto_response,
            self._options.error_code,
        )
        if frame is None:
            return

        threading.Thread(
            target=self._delayed_send_auto_response,
            args=(frame,),
            name="mock-auto-response",
            daemon=True,
        ).start()

    def _delayed_send_auto_response(self, frame: bytes) -> None:
        if self._stop_event.wait(self._options.response_delay):
            return
        self._send_it_frame(frame, f"自动应答 {frame.hex(' ')}")

    def _send_it_frame(self, frame: bytes, reason: str) -> None:
        it_serial = self._require_it_serial()
        it_serial.write(frame)
        if hasattr(it_serial, "flush"):
            it_serial.flush()
        self._logger.info(
            "IT -> APP: %s (%s)",
            format_frame_bytes(frame),
            reason,
        )

    def _open_serial(self, device: str) -> object:
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("未安装 pyserial，无法启动 mock 串口工具。") from exc

        return serial.Serial(
            port=device,
            baudrate=self._options.baudrate,
            timeout=self._options.timeout,
        )

    def _require_it_serial(self) -> object:
        if self._it_serial is None:
            raise RuntimeError("IT 串口尚未打开。")
        return self._it_serial

    def _require_scanner_serial(self) -> object:
        if self._scanner_serial is None:
            raise RuntimeError("扫码枪串口尚未打开。")
        return self._scanner_serial

    def _print_help(self) -> None:
        self._logger.info(
            "命令: barcode <text> | start-clean | error [0x33] | "
            "mode <none|start-clean|error> | status | help | quit"
        )


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, options.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    MockSerialLab(options).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
