from __future__ import annotations

import logging

from .config import AppConfig
from .gpio import GpioManager
from .protocol import Cm4WorkflowController, ProtocolHandler
from .serial_worker import SerialFactory, SerialWorker


class Cm4ControllerApp:
    """应用编排层，统一管理 GPIO 和两个串口工作器。"""

    def __init__(
        self,
        config: AppConfig,
        protocol_handler: ProtocolHandler | None = None,
        gpio_manager: GpioManager | None = None,
        serial_factory: SerialFactory | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._logger = logger or logging.getLogger(__name__)
        self._protocol_handler = protocol_handler or Cm4WorkflowController(
            config.workflow,
            logger=self._logger,
        )
        self._gpio_manager = gpio_manager or GpioManager(config.gpio_lines)
        self._workers = {
            serial_config.name: SerialWorker(
                config=serial_config,
                protocol_handler=self._protocol_handler,
                serial_factory=serial_factory,
                logger=self._logger.getChild(serial_config.name),
            )
            for serial_config in config.serial_ports
        }
        if hasattr(self._protocol_handler, "bind"):
            self._protocol_handler.bind(
                send_callback=self.send,
                read_gpio_callback=self.read_gpio,
                write_gpio_callback=self.write_gpio,
            )
        self._started = False

    def start(self) -> None:
        if self._started:
            return

        started_workers: list[SerialWorker] = []
        self._gpio_manager.initialize()
        try:
            for worker in self._workers.values():
                worker.start()
                started_workers.append(worker)
            if hasattr(self._protocol_handler, "start"):
                self._protocol_handler.start()
        except Exception:
            if hasattr(self._protocol_handler, "stop"):
                self._protocol_handler.stop()
            for worker in reversed(started_workers):
                worker.stop()
            self._gpio_manager.close()
            raise

        self._started = True
        self._logger.info("CM4 控制骨架已启动。")

    def stop(self) -> None:
        if not self._started:
            return

        if hasattr(self._protocol_handler, "stop"):
            self._protocol_handler.stop()
        for worker in self._workers.values():
            worker.stop()
        self._gpio_manager.close()
        self._started = False
        self._logger.info("CM4 控制骨架已停止。")

    def send(self, port_name: str, payload: bytes) -> None:
        self._workers[port_name].send(payload)

    def read_gpio(self, line_name: str) -> int:
        return self._gpio_manager.read(line_name)

    def write_gpio(self, line_name: str, value: int) -> None:
        self._gpio_manager.write(line_name, value)
