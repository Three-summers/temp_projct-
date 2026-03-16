from __future__ import annotations

import unittest

from cm4_skeleton.config import GpioLineConfig
from cm4_skeleton.gpio import GpioManager


class FakeDevice:
    def __init__(self, value: int = 0) -> None:
        self.value = value
        self.closed = False

    def on(self) -> None:
        self.value = 1

    def off(self) -> None:
        self.value = 0

    def close(self) -> None:
        self.closed = True


class FakeBackend:
    def setup_line(self, config: GpioLineConfig) -> FakeDevice:
        return FakeDevice(config.initial_value)

    def write(self, device: FakeDevice, value: int) -> None:
        device.value = int(value)

    def read(self, device: FakeDevice, config: GpioLineConfig) -> int:
        return int(device.value)

    def close(self, device: FakeDevice) -> None:
        device.close()


class GpioManagerTests(unittest.TestCase):
    def test_reserved_and_assigned_lines(self) -> None:
        manager = GpioManager(
            [
                GpioLineConfig("gpio_1", 17, "out", initial_value=1),
                GpioLineConfig("gpio_2", None, "out"),
                GpioLineConfig("gpio_3", 18, "in"),
            ],
            backend=FakeBackend(),
        )

        manager.initialize()

        self.assertEqual(manager.assigned_lines(), ["gpio_1", "gpio_3"])
        self.assertEqual(manager.unassigned_lines(), ["gpio_2"])
        self.assertEqual(manager.read("gpio_1"), 1)

    def test_write_output_and_close(self) -> None:
        manager = GpioManager(
            [GpioLineConfig("gpio_1", 17, "out")],
            backend=FakeBackend(),
        )

        manager.initialize()
        manager.write("gpio_1", 1)

        self.assertEqual(manager.read("gpio_1"), 1)
        manager.close()
        with self.assertRaisesRegex(RuntimeError, "未初始化"):
            manager.read("gpio_1")

    def test_reject_write_to_input_line(self) -> None:
        manager = GpioManager(
            [GpioLineConfig("gpio_1", 18, "in")],
            backend=FakeBackend(),
        )
        manager.initialize()

        with self.assertRaisesRegex(ValueError, "输出模式"):
            manager.write("gpio_1", 1)


if __name__ == "__main__":
    unittest.main()
