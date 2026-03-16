from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from .config import GpioLineConfig


class GpioBackend(Protocol):
    def setup_line(self, config: GpioLineConfig) -> object:
        """根据配置创建 GPIO 设备对象。"""

    def write(self, device: object, value: int) -> None:
        """写入 GPIO 电平。"""

    def read(self, device: object, config: GpioLineConfig) -> int:
        """读取 GPIO 电平。"""

    def close(self, device: object) -> None:
        """释放 GPIO 资源。"""


class GpioZeroBackend:
    """使用 gpiozero 提供简单稳定的 GPIO 访问层。"""

    def __init__(self) -> None:
        try:
            from gpiozero import DigitalInputDevice, OutputDevice
        except ImportError as exc:
            raise RuntimeError("未安装 gpiozero，无法初始化 GPIO。") from exc

        self._digital_input_device = DigitalInputDevice
        self._output_device = OutputDevice

    def setup_line(self, config: GpioLineConfig) -> object:
        if config.pin is None:
            raise RuntimeError(f"GPIO {config.name} 尚未分配具体引脚。")

        if config.direction == "out":
            return self._output_device(
                config.pin,
                active_high=not config.active_low,
                initial_value=bool(config.initial_value),
            )

        return self._digital_input_device(config.pin, pull_up=False)

    def write(self, device: object, value: int) -> None:
        normalized = int(value)
        if normalized not in (0, 1):
            raise ValueError("GPIO 写入值仅支持 0 或 1。")

        if normalized:
            device.on()
        else:
            device.off()

    def read(self, device: object, config: GpioLineConfig) -> int:
        raw_value = int(device.value)
        if config.direction == "in" and config.active_low:
            return 0 if raw_value else 1
        return raw_value

    def close(self, device: object) -> None:
        device.close()


class GpioManager:
    """统一管理 6 路 GPIO 预留、初始化和读写。"""

    def __init__(
        self,
        line_configs: Iterable[GpioLineConfig],
        backend: GpioBackend | None = None,
    ) -> None:
        self._line_configs = {config.name: config for config in line_configs}
        self._backend = backend or GpioZeroBackend()
        self._devices: dict[str, object | None] = {
            name: None for name in self._line_configs
        }
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return

        for config in self._line_configs.values():
            if config.pin is None:
                continue
            self._devices[config.name] = self._backend.setup_line(config)

        self._initialized = True

    def reserved_lines(self) -> list[str]:
        return list(self._line_configs)

    def assigned_lines(self) -> list[str]:
        return [
            config.name
            for config in self._line_configs.values()
            if config.pin is not None
        ]

    def unassigned_lines(self) -> list[str]:
        return [
            config.name
            for config in self._line_configs.values()
            if config.pin is None
        ]

    def write(self, line_name: str, value: int) -> None:
        config = self._get_config(line_name)
        if config.direction != "out":
            raise ValueError(f"GPIO {line_name} 不是输出模式。")

        device = self._require_device(config)
        self._backend.write(device, value)

    def read(self, line_name: str) -> int:
        config = self._get_config(line_name)
        device = self._require_device(config)
        return self._backend.read(device, config)

    def close(self) -> None:
        for name, device in self._devices.items():
            if device is not None:
                self._backend.close(device)
                self._devices[name] = None
        self._initialized = False

    def _get_config(self, line_name: str) -> GpioLineConfig:
        try:
            return self._line_configs[line_name]
        except KeyError as exc:
            raise KeyError(f"未找到 GPIO 预留项: {line_name}") from exc

    def _require_device(self, config: GpioLineConfig) -> object:
        device = self._devices[config.name]
        if device is None:
            raise RuntimeError(f"GPIO {config.name} 尚未初始化或未分配引脚。")
        return device
