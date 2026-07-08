# LatencyGate-ML

低延迟行情处理 + FPGA 加速 ML 推理系统。完整背景、架构原理和分阶段目标见 [PROJECT_PLAN.md](PROJECT_PLAN.md) —— 本文件只做导航和当前进度快照，避免和主计划文档重复维护。

## 数据流程图

**AX7A200B**（纯 PL，无 PS）跑行情接入 → 协议解析 → 订单簿重构 → 特征提取 → 风控 → 订单出口的全 RTL 流水线；**Pynq Z1**（PS+PL）的 PL 侧跑 FINN 编译的量化神经网络做推理决策；两板之间走板间直连链路（不经以太网/Linux 网络栈）；主机经 PCIe 做配置和监控，不在延迟关键路径上。

## 仓库结构

```
├── PROJECT_PLAN.md          # 完整背景、分工原则、分阶段目标（权威文档）
├── docs/
│   ├── architecture.md      # 架构图 + 当前实现状态
│   ├── protocol_spec.md     # FM24（仿ITCH）消息格式规格
│   ├── board_link_spec.md   # 板间链路协议（PMOD 接口定义）
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

`hardware/ax7a200b/` 下已经有一版可用的 feed handler 流水线，对应 PROJECT_PLAN 阶段 1-2 的部分内容：

| 文件 | 作用 |
|---|---|
| `rtl/fm24_pkg.sv` | 自定义 24 字节仿 ITCH 消息格式（FM24）的类型定义、消息/校验/TOB struct |
| `rtl/msg_parser.sv` | 流式解析 FM24 消息 |
| `rtl/book_update.sv` | 用 BRAM 价格窗口维护买卖盘状态 |
| `rtl/priority_encoder.sv` | 从 bid/ask mask 找最优价位地址 |
| `rtl/tob_tracker.sv` | 汇总输出 top-of-book |
| `rtl/latency_counter.sv` | 输入→TOB 更新的周期数延迟统计 |
| `rtl/top.sv` | 上面几个模块的顶层拼接 |
| `rtl/feed_handler.sv` | 把 `top` 封装成 AXI4-Stream + AXI4-Lite IP（可打包进 Vivado Block Design） |
| `src/fm24.py` | FM24 消息的 Python 编解码模型，和 RTL 的 struct 定义一一对应 |
| `src/test_feed.py` | **Pynq Z1** 上的 bring-up 驱动脚本，通过 AXI DMA 灌入消息、读 AXI-Lite 寄存器验证 TOB/延迟 |

**注意**：这版 `feed_handler` 是先在 **Pynq Z1**（有 PS，AXI-Lite/DMA 现成好调试）上做 bring-up 验证的，最终目标平台是 **AX7A200B**（无 PS，只能经 PCIe/XDMA）。等这套逻辑验证稳定后，需要把 AXI-Lite 控制面换成 PCIe/XDMA 寄存器接口才能移植到 AX7A200B 上——这是后续要做的适配工作，不是简单复制。

尚未开始：`feature_extract`、`risk_core`、`order_encoder`、`pcie_xdma`、`board_link_tx`（AX7A200B 侧），以及 `board_link_rx`、FINN overlay（Pynq 侧），和 `ml/`、`software/`、`verification/`、`benchmarks/` 下的全部内容。


