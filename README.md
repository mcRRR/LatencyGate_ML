# LatencyGate-ML

**A full-RTL NASDAQ ITCH 5.0 feed handler on an Artix-7 FPGA** — byte-serial
protocol decode, order-book reconstruction, and six fixed-point microstructure
features, running on real hardware against real exchange capture data.

`100 MHz timing met (WNS +0.333 ns)` · `0 of 740 DSP slices` · `9.4 % LUT` ·
`15 testbenches, 275 assertions, 0 failures` · `bit-exact Python golden model`

The longer-term goal is a two-board system that pairs this handler with an
FINN-compiled quantised neural network on a Pynq Z1 — see
[PROJECT_PLAN.md](PROJECT_PLAN.md). **This repository's working, measured
component is the feed handler**, and that is what the numbers above describe.

---

## What it does

```
ITCH bytes ──► itch_parser ──► event_dispatcher ──┬──► order_lookup   (resolve id → price/side)
                                                   └──► book_update    (price-level book, BRAM)
                                                              │
                                                     occupancy masks
                                                              ▼
                                                    priority_encoder   (radix-32 tree, 2 cyc)
                                                              ▼
                                                      tob_tracker      (top-of-book snapshot)
                                                              ▼
                                                    feature_engine     (6 × int16, 1 cycle)
                                                              ▼
                                                    board_link_tx      (15-byte framed stream)
```

The pipeline decodes NASDAQ's `[2-byte length][body]` framing, extracts fields
for the seven book-affecting message types (`A F E C X D U`), maintains an
aggregate price-level book in block RAM, tracks top-of-book, and emits a
six-dimensional fixed-point feature vector per book-changing event:
spread, top-of-book imbalance, order-flow imbalance, EMA deviation, momentum,
and trade flow.

A separate diagnostic channel reports seven pipeline counters over the same
link, because on a device with no processor and no debugger, counters are the
debugger.

---

## Results

Full detail, including how to reproduce every figure: **[docs/results.md](docs/results.md)**

### Timing — 100 MHz met on `xc7a200tfbg484-2`

| Metric | Value |
|---|---|
| WNS / TNS (setup) | **+0.333 ns** / 0.000 ns |
| WHS / THS (hold) | +0.068 ns / 0.000 ns |
| Failing endpoints | **0 of 38,013** |

The critical path was the price→address divide in `book_update`, initially at
**WNS −0.055 ns**. Splitting it into two pipeline stages — bounds check and
subtract, then the divide alone — both isolated the divider *and* narrowed it
from 32 bits to 17, because establishing in-window-ness first bounds the
difference. That is the change that closed timing.

### Resources

| Resource | Used | Available | Util |
|---|---|---|---|
| Slice LUTs | 12,577 | 133,800 | 9.40 % |
| Slice Registers | 6,211 | 267,600 | 2.32 % |
| Block RAM tiles | 58 | 365 | 15.89 % |
| **DSP slices** | **0** | 740 | **0.00 %** |

**Zero DSPs is the number worth pausing on.** Every feature is computed with
adds, subtracts and shifts — no multiplier, no divider anywhere in the feature
path. Three decisions make that possible: prices are carried as *window tick
indices* rather than raw price words (so differences are already in ticks),
imbalance is defined as a difference rather than a ratio (a ratio needs a
divider), and the EMA coefficient is 1/16 so its update is a pure arithmetic
shift. The utilisation report proves the property rather than the
documentation claiming it.

### Verification

15 unit testbenches, **275 assertions, 0 failures**, one command:

```bash
cd hardware/ax7a200b && bash tb/run_all_tb.sh
```

Two of those testbenches have been validated by **mutation testing** —
reverting the RTL fix and confirming the test actually fails. An assertion
never observed to fail is not evidence that it works.

### Latency

Core-only latency is **analytical (~18 cycles ≈ 180 ns for an Execute), not yet
measured on hardware.** The measurement instrument
(`rtl/ITCH50_parser/latency_probe.sv`) is written and unit-verified but not yet
integrated. [docs/results.md §5](docs/results.md#5-latency) states the exact
measurement definition and the remaining work.

This matters because the bring-up transport is a 1 Mbaud UART at 10 µs per
byte — roughly **three orders of magnitude slower than the core**. Any
end-to-end figure from this bitstream would measure the UART, not the feed
handler, so the probe brackets the core explicitly and separately records the
transport interval so both can be reported from the same run.

---

## Design decisions worth reading about

| Decision | Why |
|---|---|
| **Radix-32 tree priority encoder** | The original 1024-way ripple chain was a combinational carry through 1024 stages. The tree evaluates 32 leaves in parallel, then selects among 32 group flags, across 2 pipeline stages. The leaf is `mask & (~mask + 1)` — two gates — because finding a set *bit* is not the same problem as comparing *values*, which is why a comparator tree would be the wrong structure here. |
| **Split address divide** | See timing above. |
| **Occupancy mask as source of truth** | Block RAM contents cannot be reset — there is no per-cell reset wiring, only writes. So a 1-bit-per-level occupancy mask (32× smaller, affordable in flip-flops, and therefore resettable) gates every read: mask clear ⇒ effective quantity zero, whatever stale bits remain in memory. The mask was needed by the priority encoder anyway, so one structure serves two purposes. |
| **Top-of-book latch at t+2** | The encoder's outputs are live wires. The original code launched reads at t+2 but re-sampled those wires at t+4 to assemble the snapshot. A Replace fires delete-then-insert ~5 cycles apart — inside that window — so update #2's address could pair with update #1's in-flight quantity, producing an internally inconsistent snapshot no single-update test would catch. |
| **Replace decomposed in the dispatcher** | `order_lookup` has deliberately no `OP_REPLACE`. `U` becomes a delete on the old id (which also recovers the side, absent from the wire) plus an insert of the new one, keeping all cross-module protocol logic in exactly one place. |
| **Drop-oldest on the output** | The pipeline back-pressures upstream all the way to the input FIFO, but the output refuses to queue: a newer feature vector replaces a pending one and increments `drop_count`. For a decision engine a stale snapshot has negative value — a hardware choice justified by a trading argument. |

---

## Running it

**Simulation** (needs Vivado's `xsim`; located automatically, or set `VIVADO_BIN`):

```bash
cd hardware/ax7a200b && bash tb/run_all_tb.sh
```

**Build the bitstream from source** — no project files are committed:

```bash
cd hardware/ax7a200b && vivado -mode batch -source create_vivado_project.tcl
```

then `launch_runs synth_1 -jobs 8` / `launch_runs impl_1 -jobs 8`.

**On hardware**: calibrate for the instrument and trading day first, since
`stock_locate` is assigned daily and must never be hard-coded across days:

```bash
python hardware/ax7a200b/src/itch_tools.py calibrate <itch-file> --ticker AAPL
```

Then set the parameters on `top_board`, rebuild, and replay a capture over the
USB-UART at 1 Mbaud with `src/uart_feed.py`.

---

## Documentation

| Document | Purpose |
|---|---|
| [docs/handler_contract.md](docs/handler_contract.md) | **Authoritative.** Protocol, feature definitions, numeric semantics. Anything that contradicts it is a bug. |
| [docs/results.md](docs/results.md) | Measured timing, resources, verification; latency methodology |
| [docs/parameters.md](docs/parameters.md) | Every parameter, classified by what breaks when it changes |
| [docs/module_ports.md](docs/module_ports.md) | Port-level reference for all modules |
| [docs/architecture.md](docs/architecture.md) | Why the design is shaped this way |
| [docs/board_link_spec.md](docs/board_link_spec.md) | 15-byte inter-board frame format |
| [PROJECT_PLAN.md](PROJECT_PLAN.md) | Long-term two-board system plan and division of labour |

`docs/protocol_spec.md` and `docs/legacy_fm24_feature_spec.md` describe the
superseded FM24 prototype in `rtl/fm24_parser/`, kept for history only.

---

## Scope

**Implemented and measured:** ITCH 5.0 parsing, order lookup, price-level book,
top-of-book tracking, six-feature extraction, frame serialisation, diagnostic
read-back, UART bring-up on real hardware, bit-exact Python golden model.

**Not implemented:** Ethernet MAC/PHY, PCIe/XDMA, risk core, order-entry
encoder, the physical board-to-board link (UART currently substitutes for it),
and any ML inference in hardware. The FINN accelerator, the Pynq Z1 receive
path, and the training pipeline under `ml/` are planned work, not results.

The handler processes **one instrument** (`FILTER_LOCATE`) within a
**1024-tick price window**, both calibrated per trading day.

---

## Repository layout

<details>
<summary>Expand</summary>

```
hardware/ax7a200b/
  rtl/ITCH50_parser/     active handler: parser, dispatcher, lookup, book,
                         encoder, tob tracker, features, board link, probe
    io/                  UART, FIFO, arbiter, status reporter, board top
    priority_encoder_v2/ radix tree encoder
  rtl/fm24_parser/       superseded prototype, history only
  tb/                    14 testbenches + run_all_tb.sh + run_tb.sh
  src/                   itch_tools.py (golden model), uart_feed.py (host)
  constraints/           XDC
hardware/pynq_z1/        planned: board_link_rx, FINN overlay
ml/                      dataset builder, features, notebooks; training TBD
software/                planned: host driver, market simulator, backtest
docs/                    see table above
```

</details>

## Collaboration

Two developers: hardware (`hardware/`) and ML (`ml/`, `software/`). Because
those trees barely intersect, branches are cut **per module, not per person**
(`feature/hw_diagnostics`, `feature/feed_handler`), merged into `main` by PR.

Changes touching a shared interface — the feature vector layout, the frame
format, `QTY_SHIFT`, `TABLE_BITS` — require review from both sides, because
[handler_contract.md](docs/handler_contract.md) §7 mandates that the document,
the RTL, the golden model, the testbenches, and the ML dataset version all move
in the same commit. Generated artefacts (Vivado projects, bitstreams, FINN
intermediates) are never committed; everything is rebuilt from source.
