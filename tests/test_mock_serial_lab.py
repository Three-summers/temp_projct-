from __future__ import annotations

import unittest

from cm4_skeleton.mock_serial_lab import (
    build_auto_response_frame,
    describe_it_message,
    encode_barcode_payload,
    parse_args,
    parse_error_code,
)
from cm4_skeleton.protocol import (
    ItMessage,
    MESSAGE_CLEAN_START,
    MESSAGE_ERROR_COMMAND,
    MESSAGE_MOVE_IN,
    build_error_command_frame,
    build_start_clean_command_frame,
)


class MockSerialLabTests(unittest.TestCase):
    def test_parse_error_code_supports_decimal_and_hex(self) -> None:
        self.assertEqual(parse_error_code("51"), 0x33)
        self.assertEqual(parse_error_code("0x33"), 0x33)

    def test_encode_barcode_payload_appends_selected_line_ending(self) -> None:
        self.assertEqual(encode_barcode_payload("EBX8CM2.1", "crlf"), b"EBX8CM2.1\r\n")
        self.assertEqual(encode_barcode_payload("EBX8CM2.1", "none"), b"EBX8CM2.1")

    def test_describe_it_message_formats_common_messages(self) -> None:
        move_in = ItMessage(
            code=MESSAGE_MOVE_IN,
            rack_id="RPTEST",
            mask_id="EBX8CM2.1",
        )
        error = ItMessage(
            code=MESSAGE_ERROR_COMMAND,
            rack_id="RPTEST",
            error_code=0x33,
        )
        clean_start = ItMessage(code=MESSAGE_CLEAN_START, rack_id="RPTEST")

        self.assertIn("Move In", describe_it_message(move_in))
        self.assertIn("mask_id=EBX8CM2.1", describe_it_message(move_in))
        self.assertIn("0x33", describe_it_message(error))
        self.assertEqual(
            describe_it_message(clean_start),
            "Clean Start rack_id=RPTEST",
        )

    def test_build_auto_response_frame_uses_configured_rack_id(self) -> None:
        move_in = ItMessage(
            code=MESSAGE_MOVE_IN,
            rack_id="WRONG",
            mask_id="EBX8CM2.1",
        )

        self.assertEqual(
            build_auto_response_frame(move_in, "RPTEST", "start-clean", 0x33),
            build_start_clean_command_frame("RPTEST"),
        )
        self.assertEqual(
            build_auto_response_frame(move_in, "RPTEST", "error", 0x33),
            build_error_command_frame("RPTEST", 0x33),
        )
        self.assertIsNone(
            build_auto_response_frame(move_in, "RPTEST", "none", 0x33)
        )

    def test_parse_args_normalizes_cli_values(self) -> None:
        options = parse_args(
            [
                "--it-device",
                "/tmp/ttyIT_B",
                "--scanner-device",
                "/tmp/ttySCAN_B",
                "--barcode",
                "EBX8CM2.1",
                "--error-code",
                "0x33",
                "--no-interactive",
            ]
        )

        self.assertEqual(options.it_device, "/tmp/ttyIT_B")
        self.assertEqual(options.scanner_device, "/tmp/ttySCAN_B")
        self.assertEqual(options.barcodes, ["EBX8CM2.1"])
        self.assertEqual(options.error_code, 0x33)
        self.assertFalse(options.interactive)


if __name__ == "__main__":
    unittest.main()
