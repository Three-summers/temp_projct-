from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import date
from pathlib import Path

from cm4_skeleton.logging_utils import (
    DailyFileHandler,
    build_daily_log_path,
    cleanup_old_log_files,
    configure_logging,
)


class LoggingUtilsTests(unittest.TestCase):
    def test_cleanup_old_log_files_keeps_latest_five(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)
            for day in range(1, 8):
                build_daily_log_path(log_dir, date(2026, 3, day)).write_text(
                    f"log-{day}",
                    encoding="utf-8",
                )
            unrelated_file = log_dir / "ignore.txt"
            unrelated_file.write_text("keep", encoding="utf-8")

            removed_files = cleanup_old_log_files(log_dir)

            self.assertEqual(
                [item.name for item in removed_files],
                ["cm4-2026-03-01.log", "cm4-2026-03-02.log"],
            )
            self.assertEqual(
                sorted(item.name for item in log_dir.glob("cm4-*.log")),
                [
                    "cm4-2026-03-03.log",
                    "cm4-2026-03-04.log",
                    "cm4-2026-03-05.log",
                    "cm4-2026-03-06.log",
                    "cm4-2026-03-07.log",
                ],
            )
            self.assertTrue(unrelated_file.exists())

    def test_daily_file_handler_rolls_over_on_date_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            current_day = [date(2026, 3, 16)]
            handler = DailyFileHandler(
                temp_dir,
                date_provider=lambda: current_day[0],
            )
            handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
            logger = logging.Logger("daily-file-handler-test", level=logging.DEBUG)
            logger.addHandler(handler)

            logger.debug("day-one")
            current_day[0] = date(2026, 3, 17)
            logger.debug("day-two")
            handler.close()

            self.assertEqual(
                build_daily_log_path(temp_dir, date(2026, 3, 16)).read_text(
                    encoding="utf-8"
                ).strip(),
                "DEBUG:day-one",
            )
            self.assertEqual(
                build_daily_log_path(temp_dir, date(2026, 3, 17)).read_text(
                    encoding="utf-8"
                ).strip(),
                "DEBUG:day-two",
            )

    def test_configure_logging_writes_debug_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_logger = logging.getLogger()
            original_handlers = list(root_logger.handlers)
            original_level = root_logger.level
            current_day = date(2026, 3, 18)

            try:
                configure_logging(
                    log_dir=temp_dir,
                    level=logging.DEBUG,
                    date_provider=lambda: current_day,
                )
                logging.getLogger("logging-utils-test").debug("debug-message")

                log_text = build_daily_log_path(temp_dir, current_day).read_text(
                    encoding="utf-8"
                )
                self.assertIn("debug-message", log_text)
                self.assertEqual(root_logger.level, logging.DEBUG)
            finally:
                for handler in list(root_logger.handlers):
                    root_logger.removeHandler(handler)
                    handler.close()
                root_logger.setLevel(original_level)
                for handler in original_handlers:
                    root_logger.addHandler(handler)


if __name__ == "__main__":
    unittest.main()
