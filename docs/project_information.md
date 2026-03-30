引脚定义：
- 串口：
    - /dev/ttyUSB0，与 IT 通信
    - /dev/ttyUSB1，条码枪通信
- 输入：GPIO7，光耦输入（从 PLC 截取数据，检测电路板是否放上去）
- 输出：
    - GPIO11，红灯
    - GPIO9，黄灯
    - GPIO10，绿灯
    - GPIO22，白灯
    - GPIO27，继电器（用于真正执行操作），是一个类似按钮的形式
    - GPIO8，按钮，用于手动控制继电器


协议定义：
```
协议采用二进制控制字节 + ASCII 定长字段格式。

通用报文结构：
[01][02][MessageCode][RackId][OptionalData][01][03]

说明：
1. 报文以 01 02 开始，以 01 03 结束。
2. MessageCode 为 1 Byte，表示业务报文类型。
3. RackId 为固定 10 Byte ASCII 字段，右侧补空格。
4. MaskId 为固定 16 Byte ASCII 字段，右侧补空格。
5. ErrorCode 为 1 Byte，表示错误代码。
6. 所有协议示例统一使用十六进制表示。
7. 目前 RackId 固定为 RPTEST，MaskId 填充的是条码枪扫描的条码信息，比如 EBX8CM2.1，少的位右侧补空格。
8. 如果现场日志按十进制打印字节，则 `0x10/0x20/0x30/0x33` 会分别显示为 `16/32/48/51`，其中错误码 `33` 的协议字节值实际应为 `0x33`，不是十进制 `33`。

报文定义：
1) Move In
   方向：Controller -> IT
   格式：01 02 10 [RackId(10)] [MaskId(16)] 01 03

2) Start Clean Command
   方向：IT -> Controller
   格式：01 02 01 [RackId(10)] 01 03

3) Clean Start
   方向：Controller -> IT
   格式：01 02 30 [RackId(10)] 01 03

4) Move Out
   方向：Controller -> IT
   格式：01 02 20 [RackId(10)] 01 03

5) Error Command
   方向：IT -> Controller
   格式：01 02 02 [RackId(10)] [ErrorCode(1)] 01 03
   错误码：当前只有 33 是表示条码扫描不对
```

操作流程：
1. 扫描条码，获取条码信息，暂存条码信息。
2. 间隔 3 秒。
3. 检测 GPIO7 是否使能，如果使能则是已经放置，失能相反，查看东西是否放上去。
4. 开始通讯：
    1. 发送 Move In，直到接收到 Start Clean Command，这里可能会接收到 Error Command。
    2. 如果接收到 Start Clean Command 则亮绿灯，发送 Clean Start，然后开始控制继电器使能，开一秒关闭，机台开始工作，直到两分半后发送 Move Out。
    3. 如果接收到 Error Command 则亮黄灯，表示条码不匹配，则不控制继电器。
5. 其他：
    1. 另有按钮 GPIO8 用于控制继电器，用于手工验证。
    2. 灯的亮灭，当绿灯亮了，直到检测东西移走后（GPIO7 失能+又经过一次条码枪），灭掉，黄灯同样。
    3. 当移出东西时也会经过一次条码枪，这一次就不需要记录，这里判断要素是 GPIO7 没有被触发。
