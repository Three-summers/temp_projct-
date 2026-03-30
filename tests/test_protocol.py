from __future__ import annotations

import time
import unittest

from cm4_skeleton.config import WorkflowConfig
from cm4_skeleton.protocol import (
    ERROR_BARCODE_MISMATCH,
    MESSAGE_CLEAN_START,
    MESSAGE_MOVE_IN,
    MESSAGE_MOVE_OUT,
    Cm4WorkflowController,
    build_clean_start_frame,
    build_error_command_frame,
    build_move_in_frame,
    build_move_out_frame,
    build_start_clean_command_frame,
    extract_it_messages,
    format_frame_bytes,
)


class FakeHardware:
    def __init__(self) -> None:
        self.gpio_state = {
            "board_sensor": 0,
            "manual_button": 0,
            "red_light": 0,
            "yellow_light": 0,
            "green_light": 0,
            "white_light": 0,
            "relay": 0,
        }
        self.sent_frames: list[tuple[str, bytes]] = []
        self.gpio_writes: list[tuple[str, int]] = []

    def send(self, port_name: str, payload: bytes) -> None:
        self.sent_frames.append((port_name, payload))

    def read_gpio(self, line_name: str) -> int:
        return int(self.gpio_state[line_name])

    def write_gpio(self, line_name: str, value: int) -> None:
        normalized = int(value)
        self.gpio_state[line_name] = normalized
        self.gpio_writes.append((line_name, normalized))


def wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class ProtocolTests(unittest.TestCase):
    def build_controller(
        self,
        hardware: FakeHardware,
        **workflow_overrides: float | str,
    ) -> Cm4WorkflowController:
        workflow_kwargs: dict[str, float | str] = {
            "rack_id": "RPTEST",
            "scan_settle_seconds": 0.05,
            "move_in_retry_seconds": 0.05,
            "relay_pulse_seconds": 0.02,
            "clean_duration_seconds": 0.05,
            "monitor_interval_seconds": 0.01,
        }
        workflow_kwargs.update(workflow_overrides)
        controller = Cm4WorkflowController(
            WorkflowConfig(**workflow_kwargs)
        )
        controller.bind(
            send_callback=hardware.send,
            read_gpio_callback=hardware.read_gpio,
            write_gpio_callback=hardware.write_gpio,
        )
        return controller

    def test_move_in_frame_codec(self) -> None:
        frame = build_move_in_frame("RPTEST", "EBX8CM2.1")
        self.assertEqual(
            frame,
            b"\x01\x02\x10RPTEST    EBX8CM2.1       \x01\x03",
        )

        parsed = extract_it_messages(
            bytearray(build_start_clean_command_frame("RPTEST"))
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].rack_id, "RPTEST")

    def test_user_samples_match_current_protocol_definition(self) -> None:
        move_in_frame = bytes(
            [
                1, 2, 16, 82, 80, 84, 69, 83, 84,
                32, 32, 32, 32, 69, 66, 88, 56, 67,
                77, 50, 46, 49, 32, 32, 32, 32, 32,
                32, 32, 1, 3,
            ]
        )
        start_clean_frame = bytes(
            [1, 2, 1, 82, 80, 84, 69, 83, 84, 32, 32, 32, 32, 1, 3]
        )
        clean_start_frame = bytes(
            [1, 2, 48, 82, 80, 84, 69, 83, 84, 32, 32, 32, 32, 1, 3]
        )
        move_out_frame = bytes(
            [1, 2, 32, 82, 80, 84, 69, 83, 84, 32, 32, 32, 32, 1, 3]
        )

        self.assertEqual(build_move_in_frame("RPTEST", "EBX8CM2.1"), move_in_frame)
        self.assertEqual(
            build_start_clean_command_frame("RPTEST"),
            start_clean_frame,
        )
        self.assertEqual(build_clean_start_frame("RPTEST"), clean_start_frame)
        self.assertEqual(build_move_out_frame("RPTEST"), move_out_frame)

        parsed_move_in = extract_it_messages(bytearray(move_in_frame))
        self.assertEqual(len(parsed_move_in), 1)
        self.assertEqual(parsed_move_in[0].rack_id, "RPTEST")
        self.assertEqual(parsed_move_in[0].mask_id, "EBX8CM2.1")

    def test_error_frame_logging_view_shows_hex_and_decimal_difference(self) -> None:
        error_frame = build_error_command_frame("RPTEST", ERROR_BARCODE_MISMATCH)

        self.assertEqual(
            error_frame,
            bytes([1, 2, 2, 82, 80, 84, 69, 83, 84, 32, 32, 32, 32, 51, 1, 3]),
        )
        self.assertNotEqual(
            error_frame,
            bytes([1, 2, 2, 82, 80, 84, 69, 83, 84, 32, 32, 32, 32, 33, 1, 3]),
        )

        formatted = format_frame_bytes(error_frame)
        self.assertIn("hex=[01 02 02 52 50 54 45 53 54 20 20 20 20 33 01 03]", formatted)
        self.assertIn("dec=[1,2,2,82,80,84,69,83,84,32,32,32,32,51,1,3]", formatted)

    def test_full_clean_cycle(self) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware)
        controller.start()

        controller.handle_received("barcode_scanner", b"EBX8CM2.1\r\n")
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart" and frame[2] == MESSAGE_MOVE_IN
                    for port_name, frame in hardware.sent_frames
                )
            )
        )

        controller.handle_received("it_uart", build_start_clean_command_frame("RPTEST"))
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart" and frame[2] == MESSAGE_CLEAN_START
                    for port_name, frame in hardware.sent_frames
                )
            )
        )
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart" and frame[2] == MESSAGE_MOVE_OUT
                    for port_name, frame in hardware.sent_frames
                )
            )
        )
        self.assertIn(("green_light", 1), hardware.gpio_writes)
        self.assertIn(("relay", 1), hardware.gpio_writes)

        hardware.gpio_state["board_sensor"] = 0
        self.assertTrue(wait_for(lambda: hardware.gpio_state["green_light"] == 0))
        self.assertEqual(controller.state, "idle")
        controller.stop()

    def test_barcode_without_board_is_ignored(self) -> None:
        hardware = FakeHardware()
        controller = self.build_controller(hardware)
        controller.start()

        controller.handle_received("barcode_scanner", b"NOBOARD\r\n")
        time.sleep(0.12)

        self.assertEqual(hardware.sent_frames, [])
        self.assertEqual(controller.state, "idle")
        controller.stop()

    def test_error_command_turns_on_yellow(self) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware)
        controller.start()

        controller.handle_received("barcode_scanner", b"BADCODE\r\n")
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart" and frame[2] == MESSAGE_MOVE_IN
                    for port_name, frame in hardware.sent_frames
                )
            )
        )

        controller.handle_received(
            "it_uart",
            build_error_command_frame("RPTEST", ERROR_BARCODE_MISMATCH),
        )
        self.assertTrue(wait_for(lambda: hardware.gpio_state["yellow_light"] == 1))
        time.sleep(0.1)
        self.assertFalse(
            any(frame[2] == MESSAGE_CLEAN_START for _, frame in hardware.sent_frames)
        )
        self.assertFalse(
            any(name == "relay" and value == 1 for name, value in hardware.gpio_writes)
        )

        hardware.gpio_state["board_sensor"] = 0
        self.assertTrue(wait_for(lambda: hardware.gpio_state["yellow_light"] == 0))
        self.assertEqual(controller.state, "idle")
        controller.stop()

    def test_outbound_scan_with_board_still_present_does_not_clear_error_light(self) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware)
        controller.start()

        controller.handle_received("barcode_scanner", b"BADCODE\r\n")
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart" and frame[2] == MESSAGE_MOVE_IN
                    for port_name, frame in hardware.sent_frames
                )
            )
        )
        controller.handle_received(
            "it_uart",
            build_error_command_frame("RPTEST", ERROR_BARCODE_MISMATCH),
        )
        self.assertTrue(wait_for(lambda: hardware.gpio_state["yellow_light"] == 1))

        controller.handle_received("barcode_scanner", b"OUTBOUND\r\n")
        time.sleep(0.08)

        self.assertEqual(hardware.gpio_state["yellow_light"], 1)
        self.assertEqual(controller.state, "awaiting_clear_after_error")
        controller.stop()

    def test_wrong_rack_id_is_ignored_and_move_in_keeps_retrying(self) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware)
        controller.start()

        controller.handle_received("barcode_scanner", b"EBX8CM2.1\r\n")
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart" and frame[2] == MESSAGE_MOVE_IN
                    for port_name, frame in hardware.sent_frames
                )
            )
        )
        initial_move_in_count = sum(
            1 for _, frame in hardware.sent_frames if frame[2] == MESSAGE_MOVE_IN
        )

        controller.handle_received("it_uart", build_start_clean_command_frame("WRONG"))
        time.sleep(0.12)

        retried_move_in_count = sum(
            1 for _, frame in hardware.sent_frames if frame[2] == MESSAGE_MOVE_IN
        )
        self.assertGreater(retried_move_in_count, initial_move_in_count)
        self.assertEqual(hardware.gpio_state["green_light"], 0)
        self.assertEqual(controller.state, "waiting_start_clean")
        controller.stop()

    def test_latest_barcode_wins_before_settle_timeout(self) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware)
        controller.start()

        controller.handle_received("barcode_scanner", b"FIRSTCODE\r\n")
        time.sleep(0.02)
        controller.handle_received("barcode_scanner", b"SECONDCODE\r\n")

        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart"
                    and frame == build_move_in_frame("RPTEST", "SECONDCODE")
                    for port_name, frame in hardware.sent_frames
                )
            )
        )
        self.assertFalse(
            any(
                port_name == "it_uart"
                and frame == build_move_in_frame("RPTEST", "FIRSTCODE")
                for port_name, frame in hardware.sent_frames
            )
        )
        controller.stop()

    def test_board_removed_before_settle_timeout_ignores_barcode(self) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware)
        controller.start()

        controller.handle_received("barcode_scanner", b"SETTLECODE\r\n")
        time.sleep(0.02)
        hardware.gpio_state["board_sensor"] = 0
        time.sleep(0.12)

        self.assertEqual(
            sum(1 for _, frame in hardware.sent_frames if frame[2] == MESSAGE_MOVE_IN),
            0,
        )
        self.assertEqual(controller.state, "idle")
        controller.stop()

    def test_board_removed_while_waiting_start_clean_stops_retry_and_clears_barcode(
        self,
    ) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware)
        controller.start()

        controller.handle_received("barcode_scanner", b"OLDCODE\r\n")
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart"
                    and frame == build_move_in_frame("RPTEST", "OLDCODE")
                    for port_name, frame in hardware.sent_frames
                )
            )
        )

        hardware.gpio_state["board_sensor"] = 0
        self.assertTrue(wait_for(lambda: controller.state == "idle"))

        move_in_count = sum(
            1
            for _, frame in hardware.sent_frames
            if frame[2] == MESSAGE_MOVE_IN
        )
        time.sleep(0.12)
        self.assertEqual(
            sum(1 for _, frame in hardware.sent_frames if frame[2] == MESSAGE_MOVE_IN),
            move_in_count,
        )

        hardware.gpio_state["board_sensor"] = 1
        time.sleep(0.12)
        self.assertEqual(
            sum(1 for _, frame in hardware.sent_frames if frame[2] == MESSAGE_MOVE_IN),
            move_in_count,
        )
        self.assertEqual(controller.state, "idle")
        controller.stop()

    def test_start_clean_after_board_removed_is_ignored(self) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware)
        controller.start()

        controller.handle_received("barcode_scanner", b"OLDCODE\r\n")
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart"
                    and frame == build_move_in_frame("RPTEST", "OLDCODE")
                    for port_name, frame in hardware.sent_frames
                )
            )
        )

        hardware.gpio_state["board_sensor"] = 0
        self.assertTrue(wait_for(lambda: controller.state == "idle"))

        controller.handle_received("it_uart", build_start_clean_command_frame("RPTEST"))
        time.sleep(0.08)

        self.assertFalse(
            any(frame[2] == MESSAGE_CLEAN_START for _, frame in hardware.sent_frames)
        )
        self.assertFalse(
            any(name == "relay" and value == 1 for name, value in hardware.gpio_writes)
        )
        self.assertEqual(hardware.gpio_state["green_light"], 0)
        self.assertEqual(controller.state, "idle")
        controller.stop()

    def test_error_after_board_removed_is_ignored(self) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware)
        controller.start()

        controller.handle_received("barcode_scanner", b"OLDCODE\r\n")
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart"
                    and frame == build_move_in_frame("RPTEST", "OLDCODE")
                    for port_name, frame in hardware.sent_frames
                )
            )
        )

        hardware.gpio_state["board_sensor"] = 0
        self.assertTrue(wait_for(lambda: controller.state == "idle"))

        controller.handle_received(
            "it_uart",
            build_error_command_frame("RPTEST", ERROR_BARCODE_MISMATCH),
        )
        time.sleep(0.08)

        self.assertEqual(hardware.gpio_state["yellow_light"], 0)
        self.assertEqual(controller.state, "idle")
        controller.stop()

    def test_new_barcode_after_board_reinsert_starts_new_cycle(self) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware)
        controller.start()

        controller.handle_received("barcode_scanner", b"OLDCODE\r\n")
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart"
                    and frame == build_move_in_frame("RPTEST", "OLDCODE")
                    for port_name, frame in hardware.sent_frames
                )
            )
        )

        hardware.gpio_state["board_sensor"] = 0
        self.assertTrue(wait_for(lambda: controller.state == "idle"))
        old_move_in_count = sum(
            1
            for _, frame in hardware.sent_frames
            if frame == build_move_in_frame("RPTEST", "OLDCODE")
        )

        hardware.gpio_state["board_sensor"] = 1
        controller.handle_received("barcode_scanner", b"NEWCODE\r\n")
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart"
                    and frame == build_move_in_frame("RPTEST", "NEWCODE")
                    for port_name, frame in hardware.sent_frames
                )
            )
        )
        self.assertEqual(
            sum(
                1
                for _, frame in hardware.sent_frames
                if frame == build_move_in_frame("RPTEST", "OLDCODE")
            ),
            old_move_in_count,
        )
        self.assertEqual(controller.state, "waiting_start_clean")
        controller.stop()

    def test_outbound_scan_after_clean_with_board_still_present_does_not_clear_green(
        self,
    ) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware)
        controller.start()

        controller.handle_received("barcode_scanner", b"CLEANCODE\r\n")
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart" and frame[2] == MESSAGE_MOVE_IN
                    for port_name, frame in hardware.sent_frames
                )
            )
        )
        controller.handle_received("it_uart", build_start_clean_command_frame("RPTEST"))
        self.assertTrue(
            wait_for(lambda: controller.state == "awaiting_clear_after_clean")
        )

        controller.handle_received("barcode_scanner", b"OUTBOUND\r\n")
        time.sleep(0.08)

        self.assertEqual(hardware.gpio_state["green_light"], 1)
        self.assertEqual(controller.state, "awaiting_clear_after_clean")
        controller.stop()

    def test_board_removed_after_clean_without_outbound_scan_clears_cycle(
        self,
    ) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware)
        controller.start()

        controller.handle_received("barcode_scanner", b"CLEANCODE\r\n")
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart" and frame[2] == MESSAGE_MOVE_IN
                    for port_name, frame in hardware.sent_frames
                )
            )
        )
        controller.handle_received("it_uart", build_start_clean_command_frame("RPTEST"))
        self.assertTrue(
            wait_for(lambda: controller.state == "awaiting_clear_after_clean")
        )

        hardware.gpio_state["board_sensor"] = 0
        self.assertTrue(wait_for(lambda: controller.state == "idle"))

        self.assertEqual(hardware.gpio_state["green_light"], 0)
        self.assertEqual(controller.state, "idle")
        controller.stop()

    def test_error_command_during_cleaning_is_ignored(self) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware, clean_duration_seconds=0.2)
        controller.start()

        controller.handle_received("barcode_scanner", b"CLEANCODE\r\n")
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart" and frame[2] == MESSAGE_MOVE_IN
                    for port_name, frame in hardware.sent_frames
                )
            )
        )
        controller.handle_received("it_uart", build_start_clean_command_frame("RPTEST"))
        self.assertTrue(wait_for(lambda: controller.state == "cleaning"))

        clean_start_count = sum(
            1 for _, frame in hardware.sent_frames if frame[2] == MESSAGE_CLEAN_START
        )
        controller.handle_received(
            "it_uart",
            build_error_command_frame("RPTEST", ERROR_BARCODE_MISMATCH),
        )
        time.sleep(0.08)

        self.assertEqual(
            sum(1 for _, frame in hardware.sent_frames if frame[2] == MESSAGE_CLEAN_START),
            clean_start_count,
        )
        self.assertEqual(hardware.gpio_state["yellow_light"], 0)
        self.assertEqual(hardware.gpio_state["green_light"], 1)
        self.assertEqual(controller.state, "cleaning")
        controller.stop()

    def test_start_clean_during_cleaning_is_ignored(self) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware, clean_duration_seconds=0.2)
        controller.start()

        controller.handle_received("barcode_scanner", b"CLEANCODE\r\n")
        self.assertTrue(
            wait_for(
                lambda: any(
                    port_name == "it_uart" and frame[2] == MESSAGE_MOVE_IN
                    for port_name, frame in hardware.sent_frames
                )
            )
        )
        controller.handle_received("it_uart", build_start_clean_command_frame("RPTEST"))
        self.assertTrue(wait_for(lambda: controller.state == "cleaning"))

        clean_start_count = sum(
            1 for _, frame in hardware.sent_frames if frame[2] == MESSAGE_CLEAN_START
        )
        controller.handle_received("it_uart", build_start_clean_command_frame("RPTEST"))
        time.sleep(0.08)

        self.assertEqual(
            sum(1 for _, frame in hardware.sent_frames if frame[2] == MESSAGE_CLEAN_START),
            clean_start_count,
        )
        self.assertEqual(hardware.gpio_state["green_light"], 1)
        self.assertEqual(controller.state, "cleaning")
        controller.stop()

    def test_manual_button_during_waiting_start_clean_does_not_change_main_state(
        self,
    ) -> None:
        hardware = FakeHardware()
        hardware.gpio_state["board_sensor"] = 1
        controller = self.build_controller(hardware, move_in_retry_seconds=0.2)
        controller.start()

        controller.handle_received("barcode_scanner", b"MANUALCODE\r\n")
        self.assertTrue(wait_for(lambda: controller.state == "waiting_start_clean"))
        move_in_count = sum(
            1 for _, frame in hardware.sent_frames if frame[2] == MESSAGE_MOVE_IN
        )

        hardware.gpio_state["manual_button"] = 1
        self.assertTrue(
            wait_for(
                lambda: any(
                    name == "relay" and value == 1
                    for name, value in hardware.gpio_writes
                )
            )
        )
        hardware.gpio_state["manual_button"] = 0
        self.assertTrue(wait_for(lambda: hardware.gpio_state["relay"] == 0))
        time.sleep(0.24)

        self.assertEqual(controller.state, "waiting_start_clean")
        self.assertGreater(
            sum(1 for _, frame in hardware.sent_frames if frame[2] == MESSAGE_MOVE_IN),
            move_in_count,
        )
        self.assertFalse(
            any(frame[2] == MESSAGE_CLEAN_START for _, frame in hardware.sent_frames)
        )
        controller.stop()

    def test_stop_turns_off_all_lights(self) -> None:
        hardware = FakeHardware()
        controller = self.build_controller(hardware)
        controller.start()

        hardware.gpio_state["red_light"] = 1
        hardware.gpio_state["yellow_light"] = 1
        hardware.gpio_state["green_light"] = 1
        hardware.gpio_state["white_light"] = 1

        controller.stop()

        self.assertEqual(hardware.gpio_state["red_light"], 0)
        self.assertEqual(hardware.gpio_state["yellow_light"], 0)
        self.assertEqual(hardware.gpio_state["green_light"], 0)
        self.assertEqual(hardware.gpio_state["white_light"], 0)

    def test_manual_button_pulses_relay(self) -> None:
        hardware = FakeHardware()
        controller = self.build_controller(hardware)
        controller.start()

        time.sleep(0.02)
        hardware.gpio_state["manual_button"] = 1
        self.assertTrue(
            wait_for(
                lambda: any(
                    name == "relay" and value == 1
                    for name, value in hardware.gpio_writes
                )
            )
        )
        hardware.gpio_state["manual_button"] = 0
        self.assertTrue(wait_for(lambda: hardware.gpio_state["relay"] == 0))
        controller.stop()


if __name__ == "__main__":
    unittest.main()
