from __future__ import annotations

import queue
import time
import unittest

from cm4_skeleton.app import Cm4ControllerApp
from cm4_skeleton.config import AppConfig, GpioLineConfig, SerialPortConfig
from cm4_skeleton.gpio import GpioManager
from cm4_skeleton.protocol import ProtocolHandler


class FakeDevice:
    def __init__(self) -> None:
        self.value = 0

    def close(self) -> None:
        return None


class FakeGpioBackend:
    def setup_line(self, config: GpioLineConfig) -> FakeDevice:
        return FakeDevice()

    def write(self, device: FakeDevice, value: int) -> None:
        device.value = value

    def read(self, device: FakeDevice, config: GpioLineConfig) -> int:
        return device.value

    def close(self, device: FakeDevice) -> None:
        device.close()


class FakeSerial:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.responses: "queue.Queue[bytes]" = queue.Queue()
        self.closed = False

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)

    def flush(self) -> None:
        return None

    def read(self, size: int) -> bytes:
        if self.closed:
            return b""
        try:
            return self.responses.get(timeout=0.05)
        except queue.Empty:
            return b""

    def close(self) -> None:
        self.closed = True


class RecorderProtocol(ProtocolHandler):
    def __init__(self) -> None:
        self.received: list[tuple[str, bytes]] = []

    def handle_received(self, port_name: str, payload: bytes) -> None:
        self.received.append((port_name, payload))


class AppTests(unittest.TestCase):
    def test_start_stop_and_send(self) -> None:
        serial_connections: list[FakeSerial] = []

        def serial_factory(config: SerialPortConfig) -> FakeSerial:
            connection = FakeSerial()
            serial_connections.append(connection)
            return connection

        config = AppConfig(
            gpio_lines=[
                GpioLineConfig(f"gpio_{index}", None, "out")
                for index in range(1, 7)
            ],
            serial_ports=[
                SerialPortConfig("uart_1", "/dev/ttyAMA0"),
                SerialPortConfig("uart_2", "/dev/ttyAMA1"),
            ],
        )
        gpio_manager = GpioManager(config.gpio_lines, backend=FakeGpioBackend())
        app = Cm4ControllerApp(
            config=config,
            protocol_handler=RecorderProtocol(),
            gpio_manager=gpio_manager,
            serial_factory=serial_factory,
        )

        app.start()
        app.send("uart_1", b"\x33")
        time.sleep(0.2)
        app.stop()

        self.assertEqual(len(serial_connections), 2)
        self.assertIn(b"\x33", serial_connections[0].writes)


if __name__ == "__main__":
    unittest.main()
