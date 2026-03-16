# CM4 控制骨架

## 说明

本项目已经按 `docs/project_information.md` 填入第一版具体业务逻辑，当前包含以下能力：

- `/dev/ttyUSB0` 用于与 IT 通讯。
- `/dev/ttyUSB1` 用于接收条码枪扫描结果。
- 按文档映射了 7 路 GPIO，包括工件检测、手动按钮、四个指示灯和继电器。
- 已实现 IT 协议报文编解码、Move In 重发、Start Clean 应答、Error 33 处理、继电器脉冲和 150 秒后 Move Out。
- 已实现工件离位自动清灯逻辑，以及 GPIO8 手动触发继电器的监控线程。
- 已实现按天写入的 DEBUG 日志文件，默认保留最近 5 天。

GPIO 底层库当前选择 `gpiozero`，原因是接口简单、在树莓派环境中常见，后续替换成本也低。

## 目录

```text
.
├── config/cm4_config.json
├── src/cm4_skeleton
│   ├── app.py
│   ├── config.py
│   ├── gpio.py
│   ├── protocol.py
│   ├── serial_worker.py
│   └── __main__.py
└── tests
```

## GPIO 与串口映射

示例配置文件位于 `config/cm4_config.json`。

- `board_sensor` -> `GPIO7`，检测电路板是否已经放上去。
- `red_light` -> `GPIO11`。
- `yellow_light` -> `GPIO9`。
- `green_light` -> `GPIO10`。
- `white_light` -> `GPIO22`。
- `relay` -> `GPIO27`。
- `manual_button` -> `GPIO8`，手工触发继电器脉冲。
- `it_uart` -> `/dev/ttyUSB0`。
- `barcode_scanner` -> `/dev/ttyUSB1`。
- 所有串口波特率强制为 `9600`。

## 协议与流程

IT 协议采用：

```text
[01][02][MessageCode][RackId][OptionalData][01][03]
```

当前已实现：

- `0x10` Move In
- `0x01` Start Clean Command
- `0x30` Clean Start
- `0x20` Move Out
- `0x02` Error Command

流程如下：

1. 条码枪扫到条码后先缓存。
2. 延迟 3 秒检查 `GPIO7`，如果工件已在位则开始向 IT 发送 Move In。
3. 在收到 `Start Clean Command` 之前会持续重发 Move In。
4. 收到 `Start Clean Command` 后点亮绿灯，发送 Clean Start，并让继电器导通 1 秒。
5. 继电器动作后等待 150 秒，再发送 Move Out。
6. 如果收到 `Error Command`，则点亮黄灯，不触发继电器。
7. 绿灯或黄灯会一直保持到工件移走，离位后自动清除。
8. `GPIO8` 的手动按钮可以随时触发继电器脉冲，便于现场验证。

## 运行

安装依赖：

```bash
pip install -e .
```

启动程序：

```bash
python -m cm4_skeleton --config config/cm4_config.json
```

如果你不想先安装包，也可以直接使用：

```bash
PYTHONPATH=src python -m cm4_skeleton --config config/cm4_config.json
```

运行时会在项目根目录创建 `logs/` 目录，并按日期写入 `cm4-YYYY-MM-DD.log`。日志级别默认是 `DEBUG`，程序启动时会自动清理，只保留最近 5 天日志文件。

## 虚拟串口联调

如果树莓派上暂时没有真实的 IT 设备和条码枪，可以用 Linux 的虚拟 tty 对做串口联调。

### 1. 创建两对虚拟串口

先安装 `socat`：

```bash
sudo apt-get update
sudo apt-get install -y socat
```

然后创建两对串口：

```bash
socat -d -d pty,raw,echo=0,link=/tmp/ttyIT_A pty,raw,echo=0,link=/tmp/ttyIT_B
socat -d -d pty,raw,echo=0,link=/tmp/ttySCAN_A pty,raw,echo=0,link=/tmp/ttySCAN_B
```

这里约定：

- `ttyIT_A` 给主程序使用，`ttyIT_B` 给 mock 工具使用
- `ttySCAN_A` 给主程序使用，`ttySCAN_B` 给 mock 工具使用

### 2. 使用 mock 配置启动主程序

仓库已经补了一个联调配置 [config/cm4_config.mock.json](/home/say/code/project/temp_projct/config/cm4_config.mock.json)，其中：

- 串口改成 `/tmp/ttyIT_A` 和 `/tmp/ttySCAN_A`
- 扫码稳定时间缩短到 `0.2` 秒
- `Move In` 重发间隔缩短到 `0.5` 秒
- 清洗计时缩短到 `5` 秒

启动主程序：

```bash
PYTHONPATH=src python3 -m cm4_skeleton --config config/cm4_config.mock.json
```

如果已经执行过 `pip install -e .`，也可以继续沿用安装后的命令。

### 3. 启动 mock 串口工具

仓库已经补了一个 mock 工具模块 [mock_serial_lab.py](/home/say/code/project/temp_projct/src/cm4_skeleton/mock_serial_lab.py)，可以直接模拟 IT 端和扫码枪端。

如果你已经安装了项目：

```bash
cm4-mock-serial \
  --it-device /tmp/ttyIT_B \
  --scanner-device /tmp/ttySCAN_B \
  --rack-id RPTEST \
  --auto-response start-clean \
  --interactive
```

如果你不想安装脚本入口：

```bash
PYTHONPATH=src python3 -m cm4_skeleton.mock_serial_lab \
  --it-device /tmp/ttyIT_B \
  --scanner-device /tmp/ttySCAN_B \
  --rack-id RPTEST \
  --auto-response start-clean \
  --interactive
```

常用参数：

- `--auto-response start-clean`：收到 `Move In` 后自动回 `Start Clean`
- `--auto-response error --error-code 0x33`：收到 `Move In` 后自动回 `Error 33`
- `--barcode EBX8CM2.1`：启动后立即发一条条码
- `--response-delay 1.0`：模拟 IT 延迟 1 秒再回包

交互模式下支持这些命令：

- `barcode <条码>`：向应用发送一次扫码结果，工具会自动追加 `CRLF`
- `start-clean`：手动发送一条 `Start Clean Command`
- `error 0x33`：手动发送一条 `Error Command`
- `mode start-clean` / `mode error` / `mode none`：切换自动应答模式
- `status`：查看当前模式
- `quit`：退出工具

### 4. 典型联调流程

正常流程：

1. 保证 `board_sensor` 对应输入处于有效在位状态，否则应用会忽略扫码。
2. 启动主程序和 mock 工具。
3. 在 mock 终端输入 `barcode EBX8CM2.1`。
4. 工具会看到应用发出的 `Move In`，随后自动回 `Start Clean`。
5. 应用回 `Clean Start` 并在 5 秒后发 `Move Out`。

错误流程：

1. 启动 mock 工具时改成 `--auto-response error --error-code 0x33`
2. 再输入 `barcode BADCODE`
3. 应用会收到 `Error 33`，进入黄灯流程，不会触发 `Clean Start`

如果要验证“手动控制 IT 回包”，可以用 `--auto-response none`，再在交互命令里手动输入 `start-clean` 或 `error 0x33`。

### 5. 注意事项

- 当前应用除了串口，还依赖 GPIO 输入。若 `board_sensor` 没有有效在位信号，扫码后不会进入 `Move In` 流程。
- 虚拟 tty 只解决串口侧联调，不会替代 GPIO 侧的物理输入状态。
- 如果看到权限错误，先检查当前用户是否有打开伪串口和 GPIO 的权限。

## 后续扩展建议

- 如果现场的条码枪不是以 `CR/LF` 结尾，需要调整 `src/cm4_skeleton/protocol.py` 中的扫码拆包规则。
- 如果 IT 端后续增加新报文类型，可以继续在 `src/cm4_skeleton/protocol.py` 中补充编解码和状态迁移。
- 红灯和白灯目前只做硬件映射，没有被业务流程主动驱动；后续可按现场需求补充语义。

## 附录 A：流程详解（2026-03-15 / Codex）

### A.1 整体结构

这套程序可以理解成 4 层结构加 1 个状态机：

1. 启动入口：`src/cm4_skeleton/__main__.py`
2. 配置模型：`src/cm4_skeleton/config.py`
3. 应用编排层：`src/cm4_skeleton/app.py`
4. 底层 IO：
   - GPIO 管理：`src/cm4_skeleton/gpio.py`
   - 串口工作器：`src/cm4_skeleton/serial_worker.py`
5. 业务状态机：`src/cm4_skeleton/protocol.py` 中的 `Cm4WorkflowController`

程序启动后会先加载 `config/cm4_config.json`，然后创建 `Cm4ControllerApp`。应用层会把 GPIO 管理器、两个串口工作器、协议控制器串起来，并把发送串口、读取 GPIO、写 GPIO 的回调绑定给协议控制器。

### A.2 启动后系统做什么

启动顺序如下：

1. 读取配置文件，校验 GPIO 数量、串口数量、名称、波特率和流程参数。
2. 初始化 GPIO。
3. 启动两个串口工作器：
   - `it_uart` 对接 IT
   - `barcode_scanner` 对接条码枪
4. 启动协议控制器。

协议控制器启动时会先把以下输出全部置为 `0`：

- `red_light`
- `yellow_light`
- `green_light`
- `white_light`
- `relay`

然后记录当前手动按钮状态，并启动一个后台监控线程，持续轮询 `manual_button`。

所以程序刚启动时的业务含义是：全部指示灯关闭，继电器关闭，等待扫码、IT 报文或手动按钮事件。

### A.3 GPIO 与串口职责

当前配置中各硬件职责如下：

- `board_sensor`：检测工件是否已经放到位
- `manual_button`：手动触发继电器脉冲
- `green_light`：表示正常进入清洗流程
- `yellow_light`：表示 IT 返回错误流程
- `relay`：向外部机台发送一个启动脉冲
- `red_light`、`white_light`：目前只做映射，流程中未主动驱动
- `it_uart`：与 IT 设备通讯
- `barcode_scanner`：接收条码枪扫描数据

每一路 GPIO 都支持 `active_low` 配置：

- 输出 GPIO：`active_low=true` 表示逻辑值 `1` 对应物理低电平使能
- 输入 GPIO：`active_low=true` 表示读取结果会自动反相

当前示例配置中，四路灯已经改为 `active_low=true`，并设置 `initial_value=1`。`relay` 仍保持原有极性配置。

### A.4 一次完整正常流程

#### 第 1 步：系统空闲

系统初始状态为 `idle`，表示当前没有工件流程在执行。

#### 第 2 步：条码枪扫到条码

条码枪串口接收到数据后，协议控制器会把数据先放进 `_barcode_buffer`，再按回车换行拆成完整条码。

这里不会立刻进入业务动作，而是先进入扫码等待阶段。

#### 第 3 步：扫码稳定等待

收到条码后，程序会：

1. 记录这次扫码序号 `scan_token`
2. 启动一个延迟线程
3. 等待 `scan_settle_seconds`

这样做是为了避免短时间内连续扫码导致旧条码和新条码同时生效。代码里实现了“后扫的码覆盖前扫的码”。

#### 第 4 步：检查工件是否在位

延迟结束后，程序会读取 `board_sensor`。

- 如果工件不在位：这次条码直接忽略，系统继续保持 `idle`
- 如果工件在位：进入下一步

也就是说，系统要求“扫码成功”和“工件已经放上去”这两个条件同时满足，才会继续。

#### 第 5 步：进入等待 IT 允许启动状态

满足条件后，程序会：

1. 保存当前条码到 `_current_mask_id`
2. 状态从 `idle` 切到 `waiting_start_clean`
3. 关闭黄灯和绿灯
4. 启动 `Move In` 重发线程

这一步的业务含义是：告诉 IT，“这个工件已经准备好，等待你发开始清洗命令”。

#### 第 6 步：持续向 IT 重发 Move In

在 `waiting_start_clean` 状态下，系统会每隔 `move_in_retry_seconds` 向 IT 发送一次 `Move In` 报文。

只要没有收到合法的 `Start Clean Command`，这个动作就会持续进行。

#### 第 7 步：收到 IT 的 Start Clean Command

如果 IT 端发来 `Start Clean Command`，程序会先检查两件事：

1. 报文里的 `rack_id` 是否与本机配置一致
2. 当前状态是否确实是 `waiting_start_clean`

只有这两个条件都满足，才会接受这条命令。

如果 `rack_id` 不匹配，程序会忽略该报文，并继续重发 `Move In`。

#### 第 8 步：进入 cleaning

收到合法的 `Start Clean Command` 后，程序会：

1. 状态切换到 `cleaning`
2. 停止 `Move In` 重发
3. 关闭黄灯
4. 点亮绿灯
5. 向 IT 发送 `Clean Start`
6. 触发继电器脉冲
7. 启动清洗计时线程

这一步表示业务上已经正式进入清洗中。

#### 第 9 步：继电器脉冲

继电器不是在整个清洗周期内一直保持导通，而是：

1. 输出 `relay = 1`
2. 等待 `relay_pulse_seconds`
3. 输出 `relay = 0`

默认是导通 1 秒。它的业务含义是“给外部设备一个启动脉冲”，而不是“整个清洗期间一直吸合”。

如果 `relay.active_low=true`，那么逻辑上的 `relay = 1` 仍表示“触发继电器”，只是实际硬件输出会变成低电平。

#### 第 10 步：清洗计时

继电器脉冲触发后，程序会单独启动一个清洗定时线程，等待 `clean_duration_seconds`。

默认等待 150 秒。

时间到后，如果当前状态仍然是 `cleaning`，程序会：

1. 把状态切到 `awaiting_clear_after_clean`
2. 向 IT 发送 `Move Out`

这里要注意，发送 `Move Out` 并不代表系统立刻回到 `idle`。

#### 第 11 步：等待出站清除

在 `awaiting_clear_after_clean` 状态下，系统认为“这次清洗流程逻辑已经结束，但工件还没有被明确清除”。

监控线程会持续读取 `board_sensor`：

- 如果工件已经不在位：自动清除当前周期，绿灯/黄灯熄灭，回到 `idle`
- 如果工件还在位：继续保持当前状态

因此，完整闭环不是“发了 `Move Out` 就结束”，而是“工件离位后系统才真正复位”。

### A.5 异常流程

如果系统当前处于 `waiting_start_clean`，但 IT 返回的是 `Error Command`，程序会：

1. 把状态切到 `awaiting_clear_after_error`
2. 停止 `Move In` 重发
3. 熄灭绿灯
4. 点亮黄灯
5. 不发送 `Clean Start`
6. 不触发继电器

也就是说，错误分支不会启动设备，只会留下一个“异常等待清除”的状态。

之后只要检测到工件离位，系统就会熄灭黄灯并回到 `idle`。

### A.6 手动按钮流程

程序启动后有一个后台监控线程持续读取 `manual_button`。

它判断的是按钮上升沿：

- 上一次读到 `0`
- 这一次读到 `1`

满足时就会触发一次继电器脉冲。

这条路径不依赖扫码，也不依赖 IT 报文。主要用途是现场验证继电器和外部机台联动是否正常。

同样地，它触发的也只是一个短脉冲，而不是持续导通。

### A.7 状态流转图（ASCII）

```text
+--------+
|  idle  |
+--------+
    |
    | 扫码完成 + 延时结束 + board_sensor=1
    v
+----------------------+
| waiting_start_clean  |
+----------------------+
    |                     \
    | 收到 Start Clean     \ 收到 Error Command
    v                       \
+-----------+                v
| cleaning  |      +---------------------------+
+-----------+      | awaiting_clear_after_error|
    |              +---------------------------+
    | clean_duration_seconds 到时            |
    v                                         |
+---------------------------+                 |
| awaiting_clear_after_clean|                 |
+---------------------------+                 |
    |                                         |
    | board_sensor=0                          |
    +-------------------------+---------------+
                              |
                              v
                         +--------+
                         |  idle  |
                         +--------+

补充说明：
- 在 `idle` 扫码但 `board_sensor=0` 时，流程不会前进，仍保持 `idle`
- 在 `waiting_start_clean` 收到错误 `rack_id` 的 Start Clean 时，会忽略报文并继续重发 `Move In`
- `manual_button` 不改变状态机主状态，只会额外触发一次继电器脉冲
```

### A.8 正常流程时序图（ASCII）

```text
参与者:
BarcodeScanner  CM4/Protocol  BoardSensor  IT  Relay  GreenLight

    |                |            |         |    |        |
    |--扫码数据------>|            |         |    |        |
    |                |--等待 scan_settle_seconds -->      |
    |                |------------读取-------->|    |      |
    |                |<-----------在位=1-------|    |      |
    |                |--Move In--------------->|    |      |
    |                |--Move In(重发)--------->|    |      |
    |                |<--Start Clean Command---|    |      |
    |                |------------------------------>|      |
    |                |  relay=1 持续 relay_pulse_seconds   |
    |                |<------------------------------|      |
    |                |------------------------------->|      |
    |                |           green_light=1               |
    |                |--Clean Start---------->|    |        |
    |                |--等待 clean_duration_seconds         |
    |                |--Move Out------------->|    |        |
    |                |  状态进入 awaiting_clear_after_clean |
    |                |                                      |
    |                |------------读取-------->|             |
    |                |<-----------离位=0-------|             |
    |                |------------------------------->|      |
    |                |           green_light=0               |
    |                |  清除当前周期，状态回到 idle         |
```

### A.9 异常流程时序图（ASCII）

```text
参与者:
BarcodeScanner  CM4/Protocol  BoardSensor  IT  Relay  YellowLight

    |                |            |         |    |        |
    |--扫码数据------>|            |         |    |        |
    |                |--等待 scan_settle_seconds -->      |
    |                |------------读取-------->|    |      |
    |                |<-----------在位=1-------|    |      |
    |                |--Move In--------------->|    |      |
    |                |<--Error Command---------|    |      |
    |                |------------------------------------->|
    |                |           yellow_light=1             |
    |                |  不触发 relay，不发 Clean Start      |
    |                |  状态进入 awaiting_clear_after_error |
    |                |                                      |
    |                |------------读取-------->|             |
    |                |<-----------离位=0-------|             |
    |                |------------------------------------->|
    |                |           yellow_light=0             |
    |                |  清除当前周期，状态回到 idle         |
```

### A.10 手动按钮时序图（ASCII）

```text
参与者:
ManualButton  CM4/MonitorThread  Relay

    |               |             |
    |---按下=1------>|             |
    |               | 检测到上升沿 |
    |               |------------>|
    |               |  relay=1    |
    |               |<------------|
    |               |  等待 relay_pulse_seconds
    |               |------------>|
    |               |  relay=0    |
    |---松开=0------>|             |

补充说明：
- 手动按钮路径不会改变主流程状态
- 手动按钮路径不会发 IT 报文
- 它只负责触发一次继电器脉冲，便于现场测试
```

### A.11 关键配置项说明

- `scan_settle_seconds`：扫码后等待多久再确认本次扫码有效
- `move_in_retry_seconds`：`Move In` 重发周期
- `relay_pulse_seconds`：继电器脉冲宽度
- `clean_duration_seconds`：清洗计时长度
- `monitor_interval_seconds`：手动按钮轮询周期
- `rack_id`：本机识别的 RackId，只有匹配的 IT 报文才会生效
- `active_low`：每一路 GPIO 的逻辑极性配置
- `logs/cm4-YYYY-MM-DD.log`：按天输出的 DEBUG 日志文件，最多保留最近 5 天

### A.12 测试覆盖到的行为

测试文件 `tests/test_protocol.py` 已覆盖以下关键行为：

- 正常完整清洗流程
- 工件未在位时扫码被忽略
- IT 返回错误时报黄灯且不触发继电器
- 错误 `rack_id` 的 Start Clean 被忽略并继续重发 `Move In`
- 扫码稳定期内后扫条码覆盖先扫条码
- 手动按钮触发继电器脉冲

因此，README 中本附录描述的主流程、异常流程、手动按钮流程，与当前测试行为是一致的。
