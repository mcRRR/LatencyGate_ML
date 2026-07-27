# LatencyGate-ML

低延迟行情处理 + FPGA 加速 ML 推理系统。背景、架构原理和分阶段目标见
[PROJECT_PLAN.md](PROJECT_PLAN.md)；当前 handler 的协议、数值和接口标准以
[docs/handler_contract.md](docs/handler_contract.md) 为准。本文件只做导航和当前
进度快照。

## 数据流程图

**AX7A200B**（纯 PL，无 PS）跑行情接入 → 协议解析 → 订单簿重构 → 特征提取 → 风控 → 订单出口的全 RTL 流水线；**Pynq Z1**（PS+PL）的 PL 侧跑 FINN 编译的量化神经网络做推理决策；两板之间走板间直连链路（不经以太网/Linux 网络栈）；主机经 PCIe 做配置和监控，不在延迟关键路径上。

## 仓库结构

```
├── PROJECT_PLAN.md          # 完整背景、分工原则和阶段规划
├── docs/
│   ├── handler_contract.md  # 当前 ITCH50 handler 与六维特征的权威合同
│   ├── architecture.md      # 架构图 + 当前实现状态
│   ├── protocol_spec.md     # 历史 FM24 原型规格
│   ├── board_link_spec.md   # 当前 15 字节板间帧格式
│   ├── legacy_fm24_feature_spec.md  # 历史 FM24/int32 特征草案
│   └── results/             # 延迟报告、帕累托曲线、资源利用率报告
├── hardware/
│   ├── ax7a200b/
│   │   ├── rtl/             # 行情解析、LOB引擎、特征提取、风控、订单出口、PCIe、板间发送
│   │   ├── src/             # 板级 bring-up 脚本、Python 消息模型（fm24.py）
│   │   ├── tb/               # 各模块 testbench
│   │   ├── constraints/      # XDC
│   │   └── build/            # Vivado tcl 构建脚本（不提交生成的工程本体）
│   └── pynq_z1/
│       ├── rtl/board_link_rx/  # 接收特征向量，接入 FINN 输入的 glue logic
│       ├── overlay/            # FINN 生成的 bitstream + driver
│       ├── constraints/
│       └── build/
├── ml/
│   ├── data/                 # 数据获取脚本（不放原始数据，见 .gitignore）
│   ├── notebooks/             # EDA
│   ├── models/{baseline_linear, quantized_nn}/
│   ├── training/               # 含 QAT
│   ├── quantization/           # 位宽扫描实验
│   └── finn_build/             # FINN 编译构建脚本
├── software/
│   ├── host_pcie_driver/      # 主机经 PCIe 与 AX7A200B 通信
│   ├── market_simulator/       # 合成/回放行情流量生成器
│   └── backtest/                # 回测框架
├── verification/
│   ├── cocotb_tests/
│   └── coverage_reports/
├── benchmarks/
│   ├── latency_measurement/
│   └── pareto_results/
└── .github/workflows/          # CI：cocotb 测试 + python 单元测试
```

## 当前进度（已有代码）

`hardware/ax7a200b/rtl/ITCH50_parser/` 已形成当前主 handler：

| 模块 | 状态 |
|---|---|
| NASDAQ ITCH 5.0 parser | 已实现，支持主要订单簿事件和单股票过滤 |
| order lookup + price-level book | 已实现，包含直接映射订单表和固定价格窗口 |
| priority encoder + TOB tracker | 已实现，包含 back-to-back 更新修复 |
| 六维 feature engine | 已实现，输出 6 × signed int16 |
| board-link TX | 已实现，输出 15 字节带序号/XOR 的大端帧 |
| Python bit-exact golden model | 已实现，入口为 `hardware/ax7a200b/src/itch_tools.py` |
| RTL testbench | 已覆盖 parser、lookup、book、TOB、feature、board-link 和 UART |
| AX7A200B UART bring-up | 已有顶层、约束、Vivado Tcl 和主机脚本 |

旧 FM24 流水线已移动到 `hardware/ax7a200b/rtl/fm24_parser/`，只作为历史原型保留。

当前下一阶段：

1. 用 `ml/data/build_itch_dataset.py` 在真实 ITCH 文件上生成特征数据；
2. 定义时间 horizon/阈值并生成 buy/hold/sell 标签；
3. 训练 baseline，标定 `QTY_SHIFT` 和输入量化范围；
4. 完成 QAT/FINN 编译；
5. 实现 Pynq Z1 `board_link_rx` 和 FINN glue logic；
6. 完成双板链路、PCIe/XDMA、风控与订单出口。
