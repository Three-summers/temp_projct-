from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .app import Cm4ControllerApp
from .config import load_config
from .logging_utils import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CM4 控制骨架启动入口")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/cm4_config.json"),
        help="配置文件路径",
    )
    parser.add_argument(
        "--log-level",
        default="DEBUG",
        help="日志级别，例如 INFO、DEBUG，默认 DEBUG",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    configure_logging(
        log_dir=Path("logs"),
        level=getattr(logging, str(args.log_level).upper(), logging.DEBUG),
    )

    app = Cm4ControllerApp(load_config(args.config))
    app.start()

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("收到退出信号，开始关闭应用。")
    finally:
        app.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
