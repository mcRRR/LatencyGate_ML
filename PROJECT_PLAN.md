# LatencyGate-ML：低延迟行情处理与FPGA加速ML推理系统

## 项目一句话定位

AX7A200B 负责从以太网接入行情、解析协议、重构订单簿、提取特征、做风控和订单出口的全 RTL 流水线；Pynq Z1 负责运行 FINN 编译的量化神经网络加速器做决策推理。两块板子通过板间链路对接，主机通过 PCIe 做配置和监控（不在延迟关键路径上）。

---

## 一个重要澄清：AX7A200B 没有 PS

这一点必须先说清楚，否则后面的分工会搞错：

- **Pynq Z1**（Zynq-7020）是真正的 **PS+PL 架构**：PS 是硬核 ARM Cortex-A9（跑 Linux + Python/PYNQ），PL 是 FPGA 逻辑fabric。两者通过芯片内部的 AXI 互联沟通。
- **AX7A200B**（Artix-7 200T）**只有 PL，没有 PS**。Artix-7 是纯逻辑 FPGA，没有硬核处理器。这块板子上"扮演 PS 角色"的其实是**通过 PCIe 连接的外部主机 PC**，不是芯片内部的处理器。如果确实想要板载"嵌入式处理器"的体验，可以在 PL 里例化一个 MicroBlaze 软核，但那本质上仍然是消耗 PL 资源的逻辑，不是独立硬核，且没必要——外部主机通过 PCIe 已经能满足配置/监控需求。

所以下面的分工表，Pynq Z1 是真正的 PS/PL 两栏，AX7A200B 是 PL + "外部主机（经PCIe）"两栏。

---

## Pynq Z1 分工

| | 职责 | 具体内容 |
|---|---|---|
| **PS**（ARM+Linux+Python） | 配置与编排 | 加载 FINN 生成的 bitstream/overlay；写入可调参数（如决策阈值）；不参与逐样本的推理过程 |
| | 开发与调试 | 在 Jupyter/PYNQ 环境下用测试向量单独验证加速器正确性，早期用软件仿真的行情数据跑通逻辑 |
| | 日志与监控 | 定期（非逐样本）拉取推理延迟统计、决策记录，供后续分析用，可以走 Pynq 板载千兆以太网（这条路径可以走 PS，因为不是关键路径） |
| **PL**（FPGA fabric） | ML 推理核心 | FINN 编译出的量化神经网络加速器，AXI-Stream 数据流接口 |
| | 板间链路接收 | 从 AX7A200B 接收特征向量的接收逻辑，**必须直接进 PL，不能绕经 PS**，否则会被 Linux/Python 的调度开销拖慢 |
| | 决策输出 | 推理结果通过板间链路发回 AX7A200B，同样走 PL 直连 |

**关键原则**：真正影响延迟数字的数据通路（特征进 → 推理 → 决策出）必须全程在 PL 内部流动，PS 只负责"开机前配置好、开机后偶尔看一眼"，不能参与每一次推理的数据搬运。如果图省事直接用 Pynq 板载以太网做板间通信，数据会被迫经过 Linux 网络栈，测出来的延迟会是微秒到毫秒级，而不是 FINN 本该有的纳秒到亚微秒级——这会让你们的延迟数字失去说服力。

**板间链路建议**：优先用 Pynq Z1 的 PMOD 或 Arduino 排针，直接接一个自定义的简单并行/串行协议到 PL 逻辑上，而不是走板载以太网。这多一些 RTL 工作量，但换来的是数字站得住脚。

---

## AX7A200B 分工

| | 职责 | 具体内容 |
|---|---|---|
| **PL**（Artix-7 fabric，全部逻辑） | 以太网 MAC/PHY | RGMII 接口，负责行情流量的收发 |
| | 行情解析器 | 状态机流式解码仿 ITCH 格式的消息（add/cancel/execute） |
| | 订单簿重构引擎 | 用 BRAM 维护多档位买卖盘状态 |
| | 特征提取模块 | 从订单簿状态计算定点特征向量（价差、失衡度等），格式需与 CS 那边的模型输入对齐 |
| | 板间链路发送 | 把特征向量通过 PMOD 直连协议发给 Pynq Z1 |
| | 风控核 | 仓位限额、防误操作检查、熔断逻辑 |
| | 订单出口编码器 | 收到 Pynq 传回的决策后，封装成仿 OUCH 格式发出 |
| | PCIe 端点（XDMA IP） | 提供给外部主机的配置/监控接口 |
| **外部主机**（经 PCIe，不算板载） | 配置下发 | 风控阈值、运行参数 |
| | 监控与日志 | 拉取延迟时间戳、资源占用状态、异常记录 |
| | 离线分析 | 结合 CS 侧的 backtest 框架做整体效果评估 |

---

## 分阶段目标

| 阶段 | 周期 | EEE 目标 | CS 目标 | 共同产出 |
|---|---|---|---|---|
| **0. 环境打底** | 2-3周 | 跑通 AX7A200B 官方以太网/PCIe demo，练习 XDC 约束和基础 testbench | 装好 Brevitas/QKeras + FINN 工具链，跑通 FINN 官方示例，开始下载 LOBSTER/Binance 数据做 EDA | 确认两条工具链都能跑通 |
| **1. 协议与基线** | 3-4周 | 实现以太网 MAC/PHY，收发通了 | 定义特征集合，训练 baseline 线性/树模型，搭 backtest 雏形 | 敲定仿ITCH消息格式 + 板间链路的特征向量格式 |
| **2. 核心RTL** | 4周 | 行情解析器 + LOB引擎 + 特征提取，逐模块配 testbench | 用离线特征做量化感知训练(QAT)，扫不同网络规模/位宽 | EEE 导出真实特征分布反馈给 CS 调整量化范围 |
| **3. ML加速器与链路** | 3-4周 | 实现板间链路发送端(AX7A200B侧) | 用FINN编译加速器；实现Pynq PL侧链路接收逻辑，接入FINN输入 | 打通端到端链路：模拟行情→解析→特征→板间→推理→决策 |
| **4. 风控与出口** | 3周 | 风控核、PCIe XDMA集成、订单出口编码器 | 开发host监控软件(Python经PCIe)，扩展backtest加入真实硬件延迟数据 | PCIe控制/监控通道打通 |
| **5. 测量与调优** | 3-4周 | 时序收敛优化，资源利用率报告 | 整理精度-延迟-资源帕累托曲线 | 对比"纯硬件路径" vs "经PCIe/主机路径"的延迟实验 |
| **6. 复盘与产出** | 2周 | 整理RTL文档、覆盖率报告 | 整理模型文档、实验结果图表 | 技术报告、README、demo录像、简历素材 |

总周期约 20-24 周（一学期到两学期），建议不要压缩节奏——每个模块配testbench这件事本身就是你们简历上最值钱的部分，不要为了赶进度跳过。

---

## 建议的仓库结构

```
latencygate-ml/
├── README.md
├── docs/
│   ├── architecture.md          # 整体架构说明（可以直接用之前讨论的架构图）
│   ├── protocol_spec.md         # 仿ITCH消息格式规格
│   ├── board_link_spec.md       # 板间链路协议规格（PMOD接口定义）
│   └── results/                 # 延迟报告、帕累托曲线图、资源利用率报告
│
├── hardware/
│   ├── ax7a200b/
│   │   ├── rtl/
│   │   │   ├── mac/
│   │   │   ├── feed_handler/
│   │   │   ├── lob_engine/
│   │   │   ├── feature_extract/
│   │   │   ├── risk_core/
│   │   │   ├── order_encoder/
│   │   │   ├── pcie_xdma/
│   │   │   └── board_link_tx/
│   │   ├── tb/                  # 每个模块对应的testbench
│   │   ├── constraints/         # XDC文件
│   │   └── build/               # Vivado工程生成脚本(tcl)，不提交生成的工程本体
│   └── pynq_z1/
│       ├── rtl/
│       │   └── board_link_rx/   # 接收特征向量，接入FINN输入的glue logic
│       ├── overlay/             # FINN生成的bitstream + driver
│       ├── constraints/
│       └── build/
│
├── ml/
│   ├── data/                    # 数据获取脚本（不放原始数据）
│   ├── notebooks/                # EDA、探索性分析
│   ├── models/
│   │   ├── baseline_linear/
│   │   └── quantized_nn/        # Brevitas/QKeras模型定义
│   ├── training/                 # 训练脚本，含QAT
│   ├── quantization/             # 位宽扫描实验脚本
│   └── finn_build/               # FINN编译构建脚本
│
├── software/
│   ├── host_pcie_driver/         # 经PCIe与AX7A200B通信的主机端程序
│   ├── market_simulator/         # 合成/回放行情流量生成器
│   └── backtest/                 # 回测框架，整合硬件真实延迟数据
│
├── verification/
│   ├── cocotb_tests/              # 跨语言仿真测试
│   └── coverage_reports/
│
├── benchmarks/
│   ├── latency_measurement/       # 硬件计数器/回环延迟测量脚本
│   └── pareto_results/            # 精度-延迟-资源三维结果数据
│
└── .github/workflows/             # CI: 跑cocotb测试、跑python模型单元测试
```

---

## 容易被忽略但很重要的检查项

- 每个 RTL 模块提交前，确认有对应的 testbench 和至少基础的覆盖率报告——这是面试官最爱问的"你怎么验证正确性"的直接证据。
- 板间链路的时钟域穿越（CDC）问题不要偷懒，两块板子的时钟不同源，跨时钟域信号必须做同步处理，否则会有亚稳态风险。
- PCIe 和以太网 MAC 两条路径不要共享同一个决策逻辑模块的输出寄存器，避免意外的资源竞争或时序冲突。
- 量化位宽的选择要有明确的实验记录（哪怕失败的组合也记下来），这些"失败尝试"在论文/报告里同样有价值。
