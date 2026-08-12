# LatencyGate-ML

**A full-RTL NASDAQ ITCH 5.0 feed handler on an Artix-7 FPGA** — byte-serial
protocol decode, order-book reconstruction, and six fixed-point microstructure
features, built from source and running on real hardware against real exchange
capture data.

`100 MHz met · WNS +0.333 ns` · `0 of 740 DSP slices` · `9.4 % LUT` ·
`15 testbenches, 275 assertions, 0 failures` ·
**`RTL ≡ golden model, bit-exact over 877,032 feature values`**

---

## What's built, and what isn't

The long-term goal is a two-board system pairing this handler with an
FINN-compiled quantised network on a Pynq Z1 ([PROJECT_PLAN.md](PROJECT_PLAN.md)).
**Today the feed handler is the part that exists and has evidence behind it**,
and every number on this page describes only that.

| | Status |
|---|---|
| ITCH 5.0 parser — 7 book-affecting message types, symbol filter | ✅ built, tested |
| Order lookup — direct-mapped table, tracks resting quantity | ✅ built, tested |
| Price-level order book in block RAM | ✅ built, tested |
| Radix-32 priority encoder ([datasheet](hardware/ax7a200b/rtl/ITCH50_parser/priority_encoder_v2/README.md)) | ✅ built, tested, characterised |
| Top-of-book tracker | ✅ built, tested |
| Six-feature engine, zero multipliers | ✅ built, tested |
| 15-byte framed output + 7-counter diagnostic channel | ✅ built, tested |
| Bit-exact Python golden model | ✅ built, tested |
| UART bring-up on AX7A200B | ✅ runs on hardware |
| Ethernet MAC · PCIe/XDMA · risk core · order encoder | ❌ not started |
| Board-to-board link, FINN accelerator, ML training | ❌ roadmap only |

The handler processes **one instrument** within a **1024-tick price window**,
both calibrated per trading day.

---

## Architecture

![Feed handler block diagram: parser, dispatcher, order lookup and book,
priority encoder, top-of-book tracker, feature engine, board link, plus a
separate diagnostic plane](docs/img/pipeline.svg)

The pipeline decodes NASDAQ's `[2-byte length][body]` framing, extracts fields
for the seven book-affecting message types (`A F E C X D U`), maintains an
aggregate price-level book in block RAM, tracks top-of-book, and emits a
six-dimensional fixed-point feature vector per book-changing event: spread,
top-of-book imbalance, order-flow imbalance, EMA deviation, momentum and trade
flow.

Two structural points the diagram is meant to make. **Exactly one event is in
flight** — `ev_ready` is high only when the dispatcher is idle, so latency and
throughput are the same number. And the **diagnostic plane is separate**: seven
counters reach the host over the same wire through a frame-atomic arbiter, but
they are not in the latency path. On a device with no processor and no
debugger, counters *are* the debugger.

---

## Results

Full detail and reproduction commands: **[docs/results.md](docs/results.md)**

### Timing — 100 MHz met on `xc7a200tfbg484-2`

| Metric | Value |
|---|---|
| WNS / TNS (setup) | **+0.333 ns** / 0.000 ns |
| WHS / THS (hold) | +0.068 ns / 0.000 ns |
| Failing endpoints | **0 of 38,013** |

The critical path was the price→address divide in `book_update`, initially at
**WNS −0.055 ns**. Splitting it across two pipeline stages — bounds check and
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
adds, subtracts and shifts — no multiplier or divider anywhere in the feature
path. Three decisions make that possible: prices are carried as *window tick
indices* rather than raw price words (so differences are already in ticks),
imbalance is a difference rather than a ratio (a ratio needs a divider), and
the EMA coefficient is 1/16 so its update is a pure arithmetic shift. The
utilisation report *proves* the property instead of the documentation claiming
it.

### Priority encoder — characterised standalone

Out-of-context synthesis, both ports, versus the ripple-chain original it
replaced:

| | radix tree @1024 | radix tree @2048 | ripple chain |
|---|---|---|---|
| **Fmax** | **161 MHz** | **126 MHz** | **15.7 MHz** |
| Latency | 2 cycles = 20 ns @100 MHz | 2 cycles | 63.7 ns combinational |
| LUT / FF | 4,546 / 406 | 8,851 / 792 | 3,875 / 0 |

**Adding two pipeline stages made it 3.2× faster** — cycles only mean something
multiplied by a period you can actually achieve, and the ripple chain was
capping the entire design at ~16 MHz. The area price was 17 % more LUTs.
Full analysis: [encoder datasheet](hardware/ax7a200b/rtl/ITCH50_parser/priority_encoder_v2/README.md).

### Verification

```bash
cd hardware/ax7a200b && bash tb/run_all_tb.sh      # 15 testbenches, 275 assertions
py hardware/ax7a200b/src/itch_tools.py selftest    # golden model vs RTL frames
```

| Suite | Result |
|---|---|
| RTL testbenches | **15 files, 275 assertions, 0 failures** |
| `itch_tools.py selftest` | **PASS** — golden model matches RTL frames |
| Python unit tests (3 suites) | **all passing** |

Two testbenches are validated by **mutation testing** — reverting the RTL fix
and confirming the test actually fails. Removing `tob_tracker`'s address latch
breaks the back-to-back test while the single-update test still passes;
replacing the latency probe's per-stage timestamps with a naive flag breaks
exactly the overlap assertions. An assertion never observed to fail is not
evidence that it works.

### Differential verification — RTL ≡ golden model

Unit tests check each module against hand-written expectations. Only an
end-to-end run on real exchange data against an *independent* implementation
can catch a shared misunderstanding of the protocol, so:

Real NASDAQ ITCH 5.0 captures are pushed through the RTL in simulation and
through the Python golden model at identical calibration, then diffed frame by
frame — one command, non-zero exit on any mismatch:

```bash
cd hardware/ax7a200b && bash tb/run_diff.sh aapl_200000.bin
```

| Capture | Messages | Frames | Feature values | Mismatches |
|---|---|---|---|---|
| `aapl_small` | 2,000 | 1,592 | 9,552 | **0** |
| `aapl_50000` | 50,000 | 31,994 | 191,964 | **0** |
| **`aapl_200000`** | **200,000** | **146,172** | **877,032** | **0** |

Coverage matters more than volume here: the 200k capture is the one that
exercises **10,196 Replace messages** — the most intricate path in the design,
decomposed into delete-then-insert across two modules — plus 122
Execute-with-Price and **14,060 order-table evictions**. That last number is
the point of the exercise: the contract requires the Python model to reproduce
the direct-mapped table's eviction behaviour rather than use an unbounded
dictionary, and 14,060 agreeing evictions is what proves it does.

Every diagnostic counter agrees independently as well — book-affecting message
count, out-of-window drops, lookup misses, parse errors, dropped frames, bad
checksums.

Note that this is a **simulation-vs-model** result: two independent
implementations of the same specification, run on a PC. It says nothing about
the silicon. An archived UART capture from an earlier board session happens to
be byte-identical to the simulation output on the small dataset, which is
encouraging, but that file has no recorded provenance and predates the current
bitstream — so it is not cited as evidence here. Confirming the FPGA matches is
a separate, still-outstanding step.

### Latency — measured

An on-chip probe (`latency_probe.sv`) timestamps every event as it travels the
pipeline. **Measurement definition**: cycles from `ev_handoff` — the parser
handing a validated event to the dispatcher — to the first `feat_valid` that
event produces. This deliberately excludes the transport, so it characterises
the core rather than whatever happens to be feeding it.

Measured over 1,592 events of real ITCH data at 100 MHz:

| | Cycles | Time |
|---|---|---|
| min | 13 | **130 ns** |
| mean | 15 | **150 ns** |
| max | 18 | **180 ns** |

Stage breakdown: `resolve` 7 · `book2tob` **5** · `tob2feat` **1**.

Those last two are fixed-latency chains with no data dependence, so the probe
doubles as an online assertion — both constants were derived from the RTL
*before* measuring, and any other value would be a bug rather than a result. A
second cross-check falls out for free: the probe counted **237 events that
produced no feature**, which is exactly 229 out-of-window drops + 8 order-table
misses.

The probe is observation-only — four taps, no handshake, no back-pressure.
Removing it cannot change the datapath, and the differential test passes
identically with it wired in.

### Not yet measured

One claim this project does **not** make yet, stated explicitly because
overclaiming is worse than a gap:

- **The differential and latency results are simulation-based, not
  hardware-verified.** The design has been brought up on an AX7A200B and emits
  frames over UART, but no board capture with recorded provenance has been
  diffed against the golden model at the current bitstream's calibration.

---

## Design decisions worth reading about

| Decision | Why |
|---|---|
| **Radix-32 tree priority encoder** | The original 1024-way ripple chain was a combinational carry through 1024 stages. The tree evaluates 32 leaves in parallel, then selects among 32 group flags, across 2 pipeline stages. The leaf is `mask & (~mask + 1)` — two gates — because finding a set *bit* is not the same problem as comparing *values*, which is why a comparator tree would be the wrong structure. |
| **Split address divide** | See timing above. |
| **Occupancy mask as source of truth** | Block RAM contents cannot be reset — there is no per-cell reset wiring, only writes. So a 1-bit-per-level mask (32× smaller, affordable in flip-flops, therefore resettable) gates every read: mask clear ⇒ effective quantity zero, whatever stale bits remain. The priority encoder needed that mask anyway, so one structure serves two purposes. |
| **Top-of-book latch at t+2** | The encoder's outputs are live wires. The original code launched reads at t+2 but re-sampled those wires at t+4 to assemble the snapshot. Replace fires delete-then-insert ~5 cycles apart — inside that window — so update #2's address could pair with update #1's in-flight quantity. An inconsistent snapshot no single-update test would catch. |
| **Replace decomposed in the dispatcher** | `order_lookup` deliberately has no `OP_REPLACE`. `U` becomes a delete on the old id (which also recovers the side, absent from the wire) plus an insert of the new one, keeping cross-module protocol logic in exactly one place. |
| **Drop-oldest on the output** | The pipeline back-pressures upstream to the input FIFO, but the output refuses to queue: a newer vector replaces a pending one and increments `drop_count`. For a decision engine a stale snapshot has negative value — a hardware choice justified by a trading argument. |
| **Byte-serial parser** | Matches a byte-oriented transport and keeps the FSM trivial, at the cost of making the parser the core's throughput limit (1 byte/cycle). A 64-bit stream from a 10G MAC would need a fundamentally different parser — a deliberate trade, not an oversight. |

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

then `launch_runs synth_1 -jobs 8` and `launch_runs impl_1 -jobs 8`.

**On hardware** — calibrate for the instrument and trading day first, since
`stock_locate` is assigned daily and must never be hard-coded across days:

```bash
py hardware/ax7a200b/src/itch_tools.py calibrate <itch-file> --ticker AAPL
```

Set the resulting parameters on `top_board`, rebuild, then replay a capture
over the USB-UART at 1 Mbaud with `src/uart_feed.py`. LED2 blinking confirms
the clock is alive and reset released; LED3 flashes per received byte.

---

## Roadmap

| Phase | Work |
|---|---|
| **Next** | Wire in the latency probe → replace analytical latency with measured min/max/mean; fresh paired hardware-vs-golden run |
| **Ethernet ingress** | `eth_crc32` is built and verified (14 assertions); remaining: frame depacketiser, Gray-coded async FIFO (the design's first real CDC), UDP header skip, RGMII source-synchronous timing |
| **ML half** | Feature dataset → labelling → baseline → quantisation-aware training → FINN compilation |
| **Two-board link** | PMOD board-link TX/RX, Pynq Z1 receive path into FINN |
| **Later** | PCIe/XDMA host channel, risk core, order-entry encoder |

---

## Documentation

| Document | Purpose |
|---|---|
| [docs/handler_contract.md](docs/handler_contract.md) | **Authoritative.** Protocol, feature definitions, numeric semantics. Anything contradicting it is a bug. |
| [docs/results.md](docs/results.md) | Measured timing, resources, verification; latency methodology |
| [docs/parameters.md](docs/parameters.md) | Every parameter, classified by what breaks when it changes |
| [docs/module_ports.md](docs/module_ports.md) | Port-level reference for all modules |
| [docs/architecture.md](docs/architecture.md) | Why the design is shaped this way |
| [encoder datasheet](hardware/ax7a200b/rtl/ITCH50_parser/priority_encoder_v2/README.md) | Standalone IP documentation for the priority encoder |
| [PROJECT_PLAN.md](PROJECT_PLAN.md) | Long-term two-board system plan |

`docs/protocol_spec.md` and `docs/legacy_fm24_feature_spec.md` describe the
superseded FM24 prototype in `rtl/fm24_parser/`, kept for history only.

---

## Repository layout

<details>
<summary>Expand</summary>

```
hardware/ax7a200b/
  rtl/ITCH50_parser/     parser, dispatcher, lookup, book, tob tracker,
                         features, board link, latency probe
    io/                  UART, FIFO, arbiter, status reporter, board top
    priority_encoder_v2/ radix tree encoder + datasheet + figures
  rtl/eth/               Ethernet building blocks (CRC-32 / FCS)
  rtl/fm24_parser/       superseded prototype, history only
  tb/                    15 testbenches, run_all_tb.sh, run_tb.sh
  src/                   itch_tools.py (golden model), uart_feed.py (host)
  constraints/           XDC
hardware/pynq_z1/        planned: board_link_rx, FINN overlay
ml/                      dataset builder, reference features, notebooks
software/                planned: host driver, market simulator, backtest
docs/                    see table above
```

</details>

## Collaboration

Two developers: hardware (`hardware/`) and ML (`ml/`, `software/`). Because
those trees barely intersect, branches are cut **per module, not per person**,
and merged into `main` by PR.

Changes touching a shared interface — the feature vector layout, the frame
format, `QTY_SHIFT`, `TABLE_BITS` — require review from both sides, because
[handler_contract.md](docs/handler_contract.md) §7 mandates that the document,
the RTL, the golden model, the testbenches and the ML dataset version all move
in the same commit. Generated artefacts (Vivado projects, bitstreams, FINN
intermediates) are never committed; everything is rebuilt from source.
