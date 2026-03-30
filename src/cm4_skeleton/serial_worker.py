from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from .config import SerialPortConfig
from .protocol import NullProtocolHandler, ProtocolHandler, format_frame_bytes


SerialFactory = Callable[[SerialPortConfig], object]


def _default_serial_factory(config: SerialPortConfig) -> object:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("未安装 pyserial，无法打开串口。") from exc

    return serial.Serial(
        port=config.device,
        baudrate=config.baudrate,
        timeout=config.timeout,
    )


class SerialWorker:
    """为单个串口提供独立发送线程和接收线程。"""

    def __init__(
        self,
        config: SerialPortConfig,
        protocol_handler: ProtocolHandler | None = None,
        serial_factory: SerialFactory | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._protocol_handler = protocol_handler or NullProtocolHandler()
        self._serial_factory = serial_factory or _default_serial_factory
        self._logger = logger or logging.getLogger(f"{__name__}.{config.name}")
        self._serial: object | None = None
        self._send_queue: queue.Queue[bytes | object] = queue.Queue()
        self._stop_event = threading.Event()
        self._tx_thread: threading.Thread | None = None
        self._rx_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop_marker = object()

    @property
    def port_name(self) -> str:
        return self._config.name

    @property
    def is_running(self) -> bool:
        return bool(
            self._tx_thread
            and self._rx_thread
            and self._tx_thread.is_alive()
            and self._rx_thread.is_alive()
        )

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return

            self._serial = self._serial_factory(self._config)
            self._stop_event.clear()
            self._tx_thread = threading.Thread(
                target=self._tx_loop,
                name=f"{self._config.name}-tx",
                daemon=True,
            )
            self._rx_thread = threading.Thread(
                target=self._rx_loop,
                name=f"{self._config.name}-rx",
                daemon=True,
            )
            self._tx_thread.start()
            self._rx_thread.start()

    def stop(self) -> None:
        with self._lock:
            serial_connection = self._serial
            tx_thread = self._tx_thread
            rx_thread = self._rx_thread
            self._serial = None
            self._tx_thread = None
            self._rx_thread = None
            self._stop_event.set()
            self._send_queue.put(self._stop_marker)

        if serial_connection is not None and hasattr(serial_connection, "close"):
            serial_connection.close()

        if tx_thread is not None:
            tx_thread.join(timeout=1.0)

        if rx_thread is not None:
            rx_thread.join(timeout=max(1.0, self._config.timeout * 5))

    def send(self, payload: bytes) -> None:
        frame = self._protocol_handler.prepare_transmit(self._config.name, payload)
        self._send_queue.put(frame)

    def _tx_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                payload = self._send_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if payload is self._stop_marker:
                break

            serial_connection = self._serial
            if serial_connection is None:
                break

            try:
                self._logger.debug(
                    "串口 %s TX: %s",
                    self._config.name,
                    format_frame_bytes(payload),
                )
                serial_connection.write(payload)
                if hasattr(serial_connection, "flush"):
                    serial_connection.flush()
            except Exception:
                self._logger.exception("串口 %s 发送线程异常。", self._config.name)
                if self._stop_event.is_set():
                    break

    def _rx_loop(self) -> None:
        while not self._stop_event.is_set():
            serial_connection = self._serial
            if serial_connection is None:
                return

            try:
                payload = serial_connection.read(self._config.read_size)
            except Exception:
                if self._stop_event.is_set():
                    return
                self._logger.exception("串口 %s 接收线程异常。", self._config.name)
                continue

            if payload:
                self._logger.debug(
                    "串口 %s RX: %s",
                    self._config.name,
                    format_frame_bytes(payload),
                )
                self._protocol_handler.handle_received(self._config.name, payload)
