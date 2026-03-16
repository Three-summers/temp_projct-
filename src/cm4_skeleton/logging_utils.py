from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TextIO


DateProvider = Callable[[], date]


def build_daily_log_path(
    log_dir: str | Path,
    current_date: date,
    prefix: str = "cm4",
) -> Path:
    return Path(log_dir) / f"{prefix}-{current_date.isoformat()}.log"


def cleanup_old_log_files(
    log_dir: str | Path,
    prefix: str = "cm4",
    keep_days: int = 5,
) -> list[Path]:
    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    log_files = sorted(log_dir_path.glob(f"{prefix}-*.log"))
    removable = log_files[:-keep_days] if len(log_files) > keep_days else []
    removed_files: list[Path] = []
    for file_path in removable:
        file_path.unlink(missing_ok=True)
        removed_files.append(file_path)
    return removed_files


class DailyFileHandler(logging.Handler):
    def __init__(
        self,
        log_dir: str | Path,
        prefix: str = "cm4",
        keep_days: int = 5,
        level: int = logging.DEBUG,
        date_provider: DateProvider | None = None,
    ) -> None:
        super().__init__(level=level)
        self._log_dir = Path(log_dir)
        self._prefix = prefix
        self._keep_days = keep_days
        self._date_provider = date_provider or date.today
        self._current_date: date | None = None
        self._stream: TextIO | None = None
        self._current_path: Path | None = None

    @property
    def current_path(self) -> Path | None:
        return self._current_path

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = self._ensure_stream()
            stream.write(f"{self.format(record)}\n")
            stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        self._close_stream()
        super().close()

    def _ensure_stream(self) -> TextIO:
        current_date = self._date_provider()
        if self._stream is None or self._current_date != current_date:
            self._open_stream(current_date)
        assert self._stream is not None
        return self._stream

    def _open_stream(self, current_date: date) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._close_stream()
        log_path = build_daily_log_path(self._log_dir, current_date, self._prefix)
        self._stream = log_path.open("a", encoding="utf-8")
        self._current_date = current_date
        self._current_path = log_path
        cleanup_old_log_files(self._log_dir, self._prefix, self._keep_days)

    def _close_stream(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None


def configure_logging(
    log_dir: str | Path = "logs",
    level: int = logging.DEBUG,
    date_provider: DateProvider | None = None,
) -> logging.Logger:
    root_logger = logging.getLogger()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    root_logger.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    file_handler = DailyFileHandler(
        log_dir=log_dir,
        level=level,
        date_provider=date_provider,
    )
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    return root_logger
