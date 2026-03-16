from __future__ import annotations

import queue
import time
import unittest

from cm4_skeleton.config import SerialPortConfig
from cm4_skeleton.protocol import ProtocolHandler
from cm4_skeleton.serial_worker import SerialWorker


class FakeSerial:
    def __init__(self, responses: list[bytes] | None = None) -> None:
        self._responses: "queue.Queue[bytes]" = queue.Queue()
        for response in responses or []:
            self._responses.put(response)
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)

    def flush(self) -> None:
        return None

    def read(self, size: int) -> bytes:
        if self.closed:
            return b""

        try:
            return self._responses.get(timeout=0.05)
        except queue.Empty:
            return b""

    def close(self) -> None:
        self.closed = True


class RecorderProtocol(ProtocolHandler):
    def __init__(self) -> None:
        self.transmitted: list[tuple[str, bytes]] = []
        self.received: list[tuple[str, bytes]] = []

    def prepare_transmit(self, port_name: str, payload: bytes) -> bytes:
        self.transmitted.append((port_name, payload))
        return b"\xAA" + payload

    def handle_received(self, port_name: str, payload: bytes) -> None:
        self.received.append((port_name, payload))


class SerialWorkerTests(unittest.TestCase):
    def test_send_and_receive_threads(self) -> None:
        fake_serial = FakeSerial(responses=[b"\x10\x20"])
        protocol = RecorderProtocol()
        worker = SerialWorker(
            SerialPortConfig(
                name="uart_1",
                device="/dev/ttyAMA0",
                baudrate=9600,
                timeout=0.1,
                read_size=32,
            ),
            protocol_handler=protocol,
            serial_factory=lambda config: fake_serial,
        )

        worker.start()
        worker.send(b"\x01\x02")
        time.sleep(0.2)
        worker.stop()

        self.assertEqual(protocol.transmitted, [("uart_1", b"\x01\x02")])
        self.assertIn(b"\xAA\x01\x02", fake_serial.writes)
        self.assertEqual(protocol.received, [("uart_1", b"\x10\x20")])

    def test_stop_before_start_is_safe(self) -> None:
        worker = SerialWorker(
            SerialPortConfig(
                name="uart_2",
                device="/dev/ttyAMA1",
                baudrate=9600,
                timeout=0.1,
                read_size=32,
            ),
            protocol_handler=RecorderProtocol(),
            serial_factory=lambda config: FakeSerial(),
        )

        worker.stop()
        self.assertFalse(worker.is_running)


if __name__ == "__main__":
    unittest.main()
