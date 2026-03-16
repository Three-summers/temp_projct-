from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


VALID_GPIO_DIRECTIONS = {"in", "out"}
EXPECTED_GPIO_COUNT = 7
EXPECTED_SERIAL_PORT_COUNT = 2
FIXED_BAUDRATE = 9600


@dataclass(slots=True)
class GpioLineConfig:
    name: str
    pin: int | None
    direction: str
    active_low: bool = False
    initial_value: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "GpioLineConfig":
        direction = str(data["direction"]).lower()
        if direction not in VALID_GPIO_DIRECTIONS:
            raise ValueError(f"GPIO 方向不合法: {direction}")

        pin = data.get("pin")
        if pin is not None:
            pin = int(pin)
            if pin < 0:
                raise ValueError("GPIO 引脚号不能小于 0。")

        initial_value = int(data.get("initial_value", 0))
        if initial_value not in (0, 1):
            raise ValueError("GPIO 初始值仅支持 0 或 1。")

        return cls(
            name=str(data["name"]),
            pin=pin,
            direction=direction,
            active_low=bool(data.get("active_low", False)),
            initial_value=initial_value,
        )


@dataclass(slots=True)
class SerialPortConfig:
    name: str
    device: str
    baudrate: int = FIXED_BAUDRATE
    timeout: float = 0.2
    read_size: int = 256

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "SerialPortConfig":
        baudrate = int(data.get("baudrate", FIXED_BAUDRATE))
        if baudrate != FIXED_BAUDRATE:
            raise ValueError(f"串口波特率必须固定为 {FIXED_BAUDRATE}。")

        read_size = int(data.get("read_size", 256))
        if read_size <= 0:
            raise ValueError("串口读取长度必须大于 0。")

        timeout = float(data.get("timeout", 0.2))
        if timeout <= 0:
            raise ValueError("串口超时时间必须大于 0。")

        return cls(
            name=str(data["name"]),
            device=str(data["device"]),
            baudrate=baudrate,
            timeout=timeout,
            read_size=read_size,
        )


@dataclass(slots=True)
class WorkflowConfig:
    rack_id: str = "RPTEST"
    it_port_name: str = "it_uart"
    barcode_port_name: str = "barcode_scanner"
    board_sensor_name: str = "board_sensor"
    manual_button_name: str = "manual_button"
    red_light_name: str = "red_light"
    yellow_light_name: str = "yellow_light"
    green_light_name: str = "green_light"
    white_light_name: str = "white_light"
    relay_name: str = "relay"
    scan_settle_seconds: float = 3.0
    move_in_retry_seconds: float = 1.0
    relay_pulse_seconds: float = 1.0
    clean_duration_seconds: float = 150.0
    monitor_interval_seconds: float = 0.05

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "WorkflowConfig":
        config = cls(
            rack_id=str(data.get("rack_id", "RPTEST")).strip() or "RPTEST",
            it_port_name=str(data.get("it_port_name", "it_uart")),
            barcode_port_name=str(data.get("barcode_port_name", "barcode_scanner")),
            board_sensor_name=str(data.get("board_sensor_name", "board_sensor")),
            manual_button_name=str(data.get("manual_button_name", "manual_button")),
            red_light_name=str(data.get("red_light_name", "red_light")),
            yellow_light_name=str(data.get("yellow_light_name", "yellow_light")),
            green_light_name=str(data.get("green_light_name", "green_light")),
            white_light_name=str(data.get("white_light_name", "white_light")),
            relay_name=str(data.get("relay_name", "relay")),
            scan_settle_seconds=float(data.get("scan_settle_seconds", 3.0)),
            move_in_retry_seconds=float(data.get("move_in_retry_seconds", 1.0)),
            relay_pulse_seconds=float(data.get("relay_pulse_seconds", 1.0)),
            clean_duration_seconds=float(data.get("clean_duration_seconds", 150.0)),
            monitor_interval_seconds=float(
                data.get("monitor_interval_seconds", 0.05)
            ),
        )

        for field_name in (
            "scan_settle_seconds",
            "move_in_retry_seconds",
            "relay_pulse_seconds",
            "clean_duration_seconds",
            "monitor_interval_seconds",
        ):
            if getattr(config, field_name) <= 0:
                raise ValueError(f"流程参数 {field_name} 必须大于 0。")

        return config

    def required_gpio_names(self) -> set[str]:
        return {
            self.board_sensor_name,
            self.manual_button_name,
            self.red_light_name,
            self.yellow_light_name,
            self.green_light_name,
            self.white_light_name,
            self.relay_name,
        }

    def required_serial_names(self) -> set[str]:
        return {self.it_port_name, self.barcode_port_name}


@dataclass(slots=True)
class AppConfig:
    gpio_lines: list[GpioLineConfig]
    serial_ports: list[SerialPortConfig]
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "AppConfig":
        workflow = WorkflowConfig.from_dict(
            dict(data.get("workflow", {}))
            if isinstance(data.get("workflow", {}), dict)
            else {}
        )
        gpio_lines = [
            GpioLineConfig.from_dict(item)
            for item in data.get("gpio_lines", [])
        ]
        serial_ports = [
            SerialPortConfig.from_dict(item)
            for item in data.get("serial_ports", [])
        ]

        if len(gpio_lines) != EXPECTED_GPIO_COUNT:
            raise ValueError(f"必须预留 {EXPECTED_GPIO_COUNT} 个 GPIO。")

        if len(serial_ports) != EXPECTED_SERIAL_PORT_COUNT:
            raise ValueError(f"必须配置 {EXPECTED_SERIAL_PORT_COUNT} 个串口。")

        gpio_names = {config.name for config in gpio_lines}
        if gpio_names != workflow.required_gpio_names():
            raise ValueError("GPIO 配置名称必须与流程要求完全一致。")

        serial_names = {config.name for config in serial_ports}
        if serial_names != workflow.required_serial_names():
            raise ValueError("串口配置名称必须与流程要求完全一致。")

        return cls(
            gpio_lines=gpio_lines,
            serial_ports=serial_ports,
            workflow=workflow,
        )

    def get_serial_port(self, name: str) -> SerialPortConfig:
        for config in self.serial_ports:
            if config.name == name:
                return config
        raise KeyError(f"未找到串口配置: {name}")


def load_config(config_path: str | Path) -> AppConfig:
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as file:
        raw_config = json.load(file)
    return AppConfig.from_dict(raw_config)
