from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cm4_skeleton.config import (
    EXPECTED_GPIO_COUNT,
    FIXED_BAUDRATE,
    load_config,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CONFIG = PROJECT_ROOT / "config" / "cm4_config.json"


class ConfigTests(unittest.TestCase):
    def test_load_sample_config(self) -> None:
        config = load_config(SAMPLE_CONFIG)
        gpio_by_name = {item.name: item for item in config.gpio_lines}

        self.assertEqual(len(config.gpio_lines), EXPECTED_GPIO_COUNT)
        self.assertEqual(len(config.serial_ports), 2)
        self.assertEqual(
            {item.name for item in config.gpio_lines},
            {
                "board_sensor",
                "manual_button",
                "red_light",
                "yellow_light",
                "green_light",
                "white_light",
                "relay",
            },
        )
        self.assertEqual(config.workflow.rack_id, "RPTEST")
        self.assertTrue(
            all(port.baudrate == FIXED_BAUDRATE for port in config.serial_ports)
        )
        self.assertEqual(
            {port.name for port in config.serial_ports},
            {"it_uart", "barcode_scanner"},
        )
        for light_name in ("red_light", "yellow_light", "green_light", "white_light"):
            self.assertTrue(gpio_by_name[light_name].active_low)
            self.assertEqual(gpio_by_name[light_name].initial_value, 1)

    def test_reject_invalid_baudrate(self) -> None:
        invalid_config = {
            "workflow": {
                "it_port_name": "it_uart",
                "barcode_port_name": "barcode_scanner",
            },
            "gpio_lines": [
                {"name": "board_sensor", "pin": 7, "direction": "in"},
                {"name": "red_light", "pin": 11, "direction": "out"},
                {"name": "yellow_light", "pin": 9, "direction": "out"},
                {"name": "green_light", "pin": 10, "direction": "out"},
                {"name": "white_light", "pin": 22, "direction": "out"},
                {"name": "relay", "pin": 27, "direction": "out"},
                {"name": "manual_button", "pin": 8, "direction": "in"},
            ],
            "serial_ports": [
                {
                    "name": "it_uart",
                    "device": "/dev/ttyUSB0",
                    "baudrate": 115200,
                },
                {
                    "name": "barcode_scanner",
                    "device": "/dev/ttyUSB1",
                    "baudrate": FIXED_BAUDRATE,
                },
            ],
        }

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as file:
            json.dump(invalid_config, file)
            temp_path = Path(file.name)

        with self.assertRaisesRegex(ValueError, "9600"):
            load_config(temp_path)


if __name__ == "__main__":
    unittest.main()
