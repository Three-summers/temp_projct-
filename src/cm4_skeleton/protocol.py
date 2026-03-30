from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from .config import WorkflowConfig


FRAME_START = b"\x01\x02"
FRAME_END = b"\x01\x03"
MESSAGE_START_CLEAN_COMMAND = 0x01
MESSAGE_ERROR_COMMAND = 0x02
MESSAGE_MOVE_IN = 0x10
MESSAGE_MOVE_OUT = 0x20
MESSAGE_CLEAN_START = 0x30
ERROR_BARCODE_MISMATCH = 0x33
RACK_ID_FIELD_LENGTH = 10
MASK_ID_FIELD_LENGTH = 16

SendCallback = Callable[[str, bytes], None]
ReadGpioCallback = Callable[[str], int]
WriteGpioCallback = Callable[[str, int], None]


@dataclass(slots=True)
class ItMessage:
    code: int
    rack_id: str
    mask_id: str | None = None
    error_code: int | None = None


class ProtocolHandler(ABC):
    """串口协议处理接口。"""

    def prepare_transmit(self, port_name: str, payload: bytes) -> bytes:
        return payload

    @abstractmethod
    def handle_received(self, port_name: str, payload: bytes) -> None:
        """处理串口接收到的原始字节流。"""


class NullProtocolHandler(ProtocolHandler):
    """默认协议实现，仅记录原始收发事件。"""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(__name__)

    def handle_received(self, port_name: str, payload: bytes) -> None:
        self._logger.debug(
            "串口 %s 收到原始数据: %s",
            port_name,
            format_frame_bytes(payload),
        )


def format_frame_bytes(payload: bytes) -> str:
    """同时输出十六进制、十进制和 ASCII 视图，便于现场对照日志。"""

    return "hex=[%s] dec=[%s] ascii=[%s]" % (
        payload.hex(" "),
        ",".join(str(value) for value in payload),
        "".join(
            chr(value) if 32 <= value < 127 else "."
            for value in payload
        ),
    )


def _encode_ascii_field(value: str, length: int) -> bytes:
    encoded = value.encode("ascii", errors="ignore")[:length]
    return encoded.ljust(length, b" ")


def build_move_in_frame(rack_id: str, mask_id: str) -> bytes:
    return (
        FRAME_START
        + bytes([MESSAGE_MOVE_IN])
        + _encode_ascii_field(rack_id, RACK_ID_FIELD_LENGTH)
        + _encode_ascii_field(mask_id, MASK_ID_FIELD_LENGTH)
        + FRAME_END
    )


def build_start_clean_command_frame(rack_id: str) -> bytes:
    return (
        FRAME_START
        + bytes([MESSAGE_START_CLEAN_COMMAND])
        + _encode_ascii_field(rack_id, RACK_ID_FIELD_LENGTH)
        + FRAME_END
    )


def build_clean_start_frame(rack_id: str) -> bytes:
    return (
        FRAME_START
        + bytes([MESSAGE_CLEAN_START])
        + _encode_ascii_field(rack_id, RACK_ID_FIELD_LENGTH)
        + FRAME_END
    )


def build_move_out_frame(rack_id: str) -> bytes:
    return (
        FRAME_START
        + bytes([MESSAGE_MOVE_OUT])
        + _encode_ascii_field(rack_id, RACK_ID_FIELD_LENGTH)
        + FRAME_END
    )


def build_error_command_frame(rack_id: str, error_code: int) -> bytes:
    return (
        FRAME_START
        + bytes([MESSAGE_ERROR_COMMAND])
        + _encode_ascii_field(rack_id, RACK_ID_FIELD_LENGTH)
        + bytes([error_code & 0xFF])
        + FRAME_END
    )


def parse_it_frame(frame: bytes) -> ItMessage:
    if not frame.startswith(FRAME_START) or not frame.endswith(FRAME_END):
        raise ValueError("IT 报文帧头或帧尾不正确。")

    code = frame[2]
    payload = frame[3:-2]

    if code in (
        MESSAGE_START_CLEAN_COMMAND,
        MESSAGE_CLEAN_START,
        MESSAGE_MOVE_OUT,
    ):
        if len(payload) != RACK_ID_FIELD_LENGTH:
            raise ValueError("IT 报文长度不正确。")
        return ItMessage(code=code, rack_id=payload.decode("ascii").rstrip())

    if code == MESSAGE_MOVE_IN:
        if len(payload) != RACK_ID_FIELD_LENGTH + MASK_ID_FIELD_LENGTH:
            raise ValueError("Move In 报文长度不正确。")
        return ItMessage(
            code=code,
            rack_id=payload[:RACK_ID_FIELD_LENGTH].decode("ascii").rstrip(),
            mask_id=payload[RACK_ID_FIELD_LENGTH:].decode("ascii").rstrip(),
        )

    if code == MESSAGE_ERROR_COMMAND:
        if len(payload) != RACK_ID_FIELD_LENGTH + 1:
            raise ValueError("Error Command 报文长度不正确。")
        return ItMessage(
            code=code,
            rack_id=payload[:RACK_ID_FIELD_LENGTH].decode("ascii").rstrip(),
            error_code=payload[RACK_ID_FIELD_LENGTH],
        )

    raise ValueError(f"未知 IT 报文类型: 0x{code:02X}")


def extract_it_messages(buffer: bytearray) -> list[ItMessage]:
    messages: list[ItMessage] = []

    while True:
        start = buffer.find(FRAME_START)
        if start < 0:
            buffer.clear()
            return messages

        if start > 0:
            del buffer[:start]

        end = buffer.find(FRAME_END, len(FRAME_START) + 1)
        if end < 0:
            return messages

        frame = bytes(buffer[: end + len(FRAME_END)])
        del buffer[: end + len(FRAME_END)]
        messages.append(parse_it_frame(frame))


def extract_barcode_messages(buffer: bytearray) -> list[str]:
    messages: list[str] = []

    while True:
        newline_positions = [
            index for index, value in enumerate(buffer) if value in (0x0A, 0x0D)
        ]
        if not newline_positions:
            return messages

        end = newline_positions[0]
        chunk = bytes(buffer[:end])
        del buffer[: end + 1]
        while buffer and buffer[0] in (0x0A, 0x0D):
            del buffer[0]

        barcode = chunk.decode("ascii", errors="ignore").strip()
        if barcode:
            messages.append(barcode)


class Cm4WorkflowController(ProtocolHandler):
    """根据项目文档实现扫码、PLC 检测与 IT 通讯流程。"""

    def __init__(
        self,
        workflow: WorkflowConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self._workflow = workflow
        self._logger = logger or logging.getLogger(__name__)
        self._send_callback: SendCallback | None = None
        self._read_gpio_callback: ReadGpioCallback | None = None
        self._write_gpio_callback: WriteGpioCallback | None = None
        self._it_buffer = bytearray()
        self._barcode_buffer = bytearray()
        self._state = "idle"
        self._current_mask_id: str | None = None
        self._scan_token = 0
        self._stop_event = threading.Event()
        self._state_lock = threading.RLock()
        self._retry_stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._move_in_thread: threading.Thread | None = None
        self._clean_timer_thread: threading.Thread | None = None
        self._relay_lock = threading.Lock()
        self._last_manual_button = 0

    @property
    def state(self) -> str:
        return self._state

    def bind(
        self,
        send_callback: SendCallback,
        read_gpio_callback: ReadGpioCallback,
        write_gpio_callback: WriteGpioCallback,
    ) -> None:
        self._send_callback = send_callback
        self._read_gpio_callback = read_gpio_callback
        self._write_gpio_callback = write_gpio_callback

    def start(self) -> None:
        self._ensure_bound()
        self._stop_event.clear()
        self._retry_stop_event.set()
        self._set_output(self._workflow.red_light_name, 0)
        self._set_output(self._workflow.yellow_light_name, 0)
        self._set_output(self._workflow.green_light_name, 0)
        self._set_output(self._workflow.white_light_name, 0)
        self._set_output(self._workflow.relay_name, 0)
        self._last_manual_button = self._read_gpio(self._workflow.manual_button_name)
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="workflow-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._retry_stop_event.set()
        self._set_output(self._workflow.red_light_name, 0)
        self._set_output(self._workflow.yellow_light_name, 0)
        self._set_output(self._workflow.green_light_name, 0)
        self._set_output(self._workflow.white_light_name, 0)
        self._set_output(self._workflow.relay_name, 0)

        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=1.0)
            self._monitor_thread = None

        if self._move_in_thread is not None:
            self._move_in_thread.join(timeout=1.0)
            self._move_in_thread = None

        if self._clean_timer_thread is not None:
            self._clean_timer_thread.join(timeout=1.0)
            self._clean_timer_thread = None

    def handle_received(self, port_name: str, payload: bytes) -> None:
        if port_name == self._workflow.barcode_port_name:
            self._barcode_buffer.extend(payload)
            for barcode in extract_barcode_messages(self._barcode_buffer):
                self._handle_barcode(barcode)
            return

        if port_name == self._workflow.it_port_name:
            self._it_buffer.extend(payload)
            try:
                messages = extract_it_messages(self._it_buffer)
            except ValueError:
                self._logger.exception("解析 IT 报文失败，当前缓冲已清空。")
                self._it_buffer.clear()
                return

            for message in messages:
                self._handle_it_message(message)
            return

        self._logger.debug("收到未识别串口 %s 的数据。", port_name)

    def _handle_barcode(self, barcode: str) -> None:
        self._logger.info("收到条码: %s", barcode)

        with self._state_lock:
            board_present = self._is_board_present()

            if self._state in {
                "awaiting_clear_after_clean",
                "awaiting_clear_after_error",
            }:
                if not board_present:
                    self._logger.info("检测到出站扫码，清除灯状态。")
                    self._clear_cycle_locked("等待清除阶段收到扫码且工件已离位")
                else:
                    self._logger.info("当前工件仍在位，忽略清除扫码。")
                return

            if self._state != "idle":
                self._logger.info("流程状态为 %s，忽略条码 %s。", self._state, barcode)
                return

            self._scan_token += 1
            scan_token = self._scan_token

        threading.Thread(
            target=self._delayed_process_barcode,
            args=(scan_token, barcode),
            name=f"barcode-{scan_token}",
            daemon=True,
        ).start()

    def _delayed_process_barcode(self, scan_token: int, barcode: str) -> None:
        if self._stop_event.wait(self._workflow.scan_settle_seconds):
            return

        board_present = self._is_board_present()
        with self._state_lock:
            if self._stop_event.is_set():
                return

            if scan_token != self._scan_token or self._state != "idle":
                return

            if not board_present:
                self._logger.info("扫码后未检测到工件在位，忽略条码 %s。", barcode)
                return

            self._current_mask_id = barcode
            self._logger.info("当前条码更新为 %s。", barcode)
            self._transition_state_locked(
                "waiting_start_clean",
                f"扫码确认成功，等待 IT 启动，barcode={barcode}",
            )
            self._retry_stop_event.clear()
            self._set_output(self._workflow.yellow_light_name, 0)
            self._set_output(self._workflow.green_light_name, 0)
            self._start_move_in_retry_locked()

    def _start_move_in_retry_locked(self) -> None:
        self._move_in_thread = threading.Thread(
            target=self._move_in_retry_loop,
            name="move-in-retry",
            daemon=True,
        )
        self._move_in_thread.start()

    def _move_in_retry_loop(self) -> None:
        while not self._stop_event.is_set() and not self._retry_stop_event.is_set():
            with self._state_lock:
                if self._state != "waiting_start_clean" or not self._current_mask_id:
                    return
                if not self._is_board_present():
                    self._logger.info("发送 Move In 前检测到工件离位，取消当前扫码周期。")
                    self._cancel_waiting_start_clean_locked("发送 Move In 前检测到工件离位")
                    return
                mask_id = self._current_mask_id

            self._send(
                self._workflow.it_port_name,
                build_move_in_frame(self._workflow.rack_id, mask_id),
            )

            if self._retry_stop_event.wait(self._workflow.move_in_retry_seconds):
                return

    def _handle_it_message(self, message: ItMessage) -> None:
        self._logger.debug(
            "收到 IT 报文: code=0x%02X rack_id=%s error=%s",
            message.code,
            message.rack_id,
            (
                f"0x{message.error_code:02X}"
                if message.error_code is not None
                else "None"
            ),
        )
        if message.rack_id != self._workflow.rack_id:
            self._logger.info("忽略 RackId 不匹配的报文: %s", message.rack_id)
            return

        if message.code == MESSAGE_START_CLEAN_COMMAND:
            self._handle_start_clean()
            return

        if message.code == MESSAGE_ERROR_COMMAND:
            self._handle_error(message.error_code or 0)
            return

        self._logger.info("收到当前流程未处理的报文类型: 0x%02X", message.code)

    def _handle_start_clean(self) -> None:
        with self._state_lock:
            if self._state != "waiting_start_clean":
                self._logger.info("当前状态 %s，忽略 Start Clean。", self._state)
                return

            self._transition_state_locked("cleaning", "收到合法 Start Clean")
            self._retry_stop_event.set()
            self._set_output(self._workflow.yellow_light_name, 0)
            self._set_output(self._workflow.green_light_name, 1)

        self._send(
            self._workflow.it_port_name,
            build_clean_start_frame(self._workflow.rack_id),
        )
        self._trigger_relay_pulse()
        self._clean_timer_thread = threading.Thread(
            target=self._clean_timer_loop,
            name="clean-timer",
            daemon=True,
        )
        self._clean_timer_thread.start()

    def _handle_error(self, error_code: int) -> None:
        with self._state_lock:
            if self._state != "waiting_start_clean":
                self._logger.info("当前状态 %s，忽略 Error Command。", self._state)
                return

            self._transition_state_locked(
                "awaiting_clear_after_error",
                f"收到 Error Command，error=0x{error_code:02X}",
            )
            self._retry_stop_event.set()
            self._set_output(self._workflow.green_light_name, 0)
            self._set_output(self._workflow.yellow_light_name, 1)

        self._logger.warning("收到 IT 错误码: 0x%02X", error_code)

    def _clean_timer_loop(self) -> None:
        if self._stop_event.wait(self._workflow.clean_duration_seconds):
            return

        with self._state_lock:
            if self._state != "cleaning":
                return
            self._transition_state_locked(
                "awaiting_clear_after_clean",
                "清洗计时结束，等待工件离位",
            )

        self._send(
            self._workflow.it_port_name,
            build_move_out_frame(self._workflow.rack_id),
        )

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            button_value = self._read_gpio(self._workflow.manual_button_name)
            if button_value and not self._last_manual_button:
                self._logger.info("检测到手动按钮按下，触发继电器脉冲。")
                self._trigger_relay_pulse()

            self._last_manual_button = button_value
            board_present = self._is_board_present()
            with self._state_lock:
                if not board_present:
                    if self._state == "waiting_start_clean":
                        self._logger.info("等待启动期间检测到工件离位，取消当前扫码周期。")
                        self._cancel_waiting_start_clean_locked(
                            "监控线程检测到等待启动阶段工件离位"
                        )
                    elif self._state in {
                        "awaiting_clear_after_clean",
                        "awaiting_clear_after_error",
                    }:
                        self._logger.info("检测到工件离位，自动清除当前灯状态。")
                        self._clear_cycle_locked("监控线程检测到工件离位")
            if self._stop_event.wait(self._workflow.monitor_interval_seconds):
                return

    def _trigger_relay_pulse(self) -> None:
        threading.Thread(
            target=self._relay_pulse_loop,
            name="relay-pulse",
            daemon=True,
        ).start()

    def _relay_pulse_loop(self) -> None:
        with self._relay_lock:
            self._set_output(self._workflow.relay_name, 1)
            interrupted = self._stop_event.wait(self._workflow.relay_pulse_seconds)
            self._set_output(self._workflow.relay_name, 0)
            if interrupted:
                return

    def _clear_cycle_locked(self, reason: str = "当前周期已清除") -> None:
        previous_mask_id = self._current_mask_id
        self._current_mask_id = None
        if previous_mask_id is not None:
            self._logger.info("清除当前条码: %s。", previous_mask_id)
        self._transition_state_locked("idle", reason)
        self._set_output(self._workflow.green_light_name, 0)
        self._set_output(self._workflow.yellow_light_name, 0)

    def _cancel_waiting_start_clean_locked(self, reason: str) -> None:
        self._retry_stop_event.set()
        self._clear_cycle_locked(reason)

    def _is_board_present(self) -> bool:
        return bool(self._read_gpio(self._workflow.board_sensor_name))

    def _send(self, port_name: str, payload: bytes) -> None:
        self._ensure_bound()
        assert self._send_callback is not None
        self._logger.debug(
            "协议发送 %s: %s",
            port_name,
            format_frame_bytes(payload),
        )
        self._send_callback(port_name, payload)

    def _read_gpio(self, line_name: str) -> int:
        self._ensure_bound()
        assert self._read_gpio_callback is not None
        value = int(self._read_gpio_callback(line_name))
        self._logger.debug("GPIO 读取 %s=%s", line_name, value)
        return value

    def _set_output(self, line_name: str, value: int) -> None:
        self._ensure_bound()
        assert self._write_gpio_callback is not None
        self._logger.debug("GPIO 写入 %s=%s", line_name, value)
        self._write_gpio_callback(line_name, value)

    def _transition_state_locked(self, new_state: str, reason: str) -> None:
        previous_state = self._state
        if previous_state == new_state:
            self._logger.debug("状态保持 %s，原因: %s", new_state, reason)
            return
        self._state = new_state
        self._logger.info(
            "状态迁移: %s -> %s，原因: %s",
            previous_state,
            new_state,
            reason,
        )

    def _ensure_bound(self) -> None:
        if (
            self._send_callback is None
            or self._read_gpio_callback is None
            or self._write_gpio_callback is None
        ):
            raise RuntimeError("协议控制器尚未绑定应用层回调。")
