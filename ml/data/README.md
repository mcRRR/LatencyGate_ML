# ITCH 特征数据构建

本目录负责把原始 NASDAQ TotalView-ITCH 5.0 文件转换为与当前 FPGA handler
位完全一致的特征数据。特征计算由
`hardware/ax7a200b/src/itch_tools.py` 中的 `Golden` 完成；
`build_itch_dataset.py` 只负责参数检查、消息筛选、流式写出和元数据记录。

## 1. 标定价格窗口

对每个交易日和 ticker 运行：

```powershell
python hardware/ax7a200b/src/itch_tools.py calibrate `
  path/to/itch-file `
  --ticker AAPL
```

记录输出中的：

```text
FILTER_LOCATE
BASE_PRICE
WINDOW_SIZE
```

`stock_locate` 是逐日分配值，不能跨交易日复用。

## 2. 构建特征数据

```powershell
python ml/data/build_itch_dataset.py `
  --input path/to/itch-file `
  --ticker AAPL `
  --locate 14 `
  --base-price 1610800 `
  --window-size 1024 `
  --qty-shift 0 `
  --table-bits 14 `
  --output ml/data/processed/aapl_features.csv
```

`--locate` 可省略；构建器始终从 Stock Directory (`R`) 消息解析当天的实际
locate。如果显式值与文件不一致，命令会失败。

默认拒绝覆盖已有 CSV 或 metadata。确认需要重新生成时加入 `--force`。

调试小样本时可以使用 `--limit N`，其含义是最多扫描前 N 条原始 ITCH 消息。

## 3. 输出

CSV 列固定为：

```text
frame,timestamp_ns,bid_idx,bid_qty,ask_idx,ask_qty,
spr,tobi,ofi,emadev,mom,tflow
```

同目录生成 `<output>.meta.json`，记录：

- ticker 和 stock locate；
- `BASE_PRICE`、`WINDOW_SIZE`、`QTY_SHIFT`、`TABLE_BITS`；
- handler Git commit；
- 输入文件名和大小；
- 扫描消息、订单事件、特征帧、越界更新和 lookup miss 数量；
- 第一条和最后一条特征的时间戳。

生成过程检查：

- 时间戳单调不减；
- 六个特征均在 signed int16 范围内；
- ticker 与 stock locate 一致；
- 窗口大小是 2 的幂；
- 至少生成一条有效特征。

CSV 采用流式写出，不会把全天所有 feature frame 同时保存在内存中。写出过程中
若发生错误，临时文件会被删除，不保留不完整数据集。

## 4. 测试

```powershell
python -m unittest discover -s ml/data -p "test_build_itch_dataset.py" -v
python -m unittest discover -s hardware/ax7a200b/src -p "test_itch_tools.py" -v
```

当前数据集只包含硬件特征，不包含 buy/hold/sell 标签。标签生成属于下一阶段。

