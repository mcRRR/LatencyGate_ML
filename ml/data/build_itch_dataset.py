#!/usr/bin/env python3
"""Build a timestamped, bit-exact feature dataset from an ITCH 5.0 file.

This command is an orchestration layer around the active handler Golden model.
It does not reimplement order-book or feature math.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hardware.ax7a200b.src.itch_tools import (  # noqa: E402
    BOOK_TYPES,
    MT_STOCK_DIR,
    TICK_SIZE,
    Golden,
    iter_messages,
    locate_of,
    parse_event,
)


FEATURE_FIELDS = ("spr", "tobi", "ofi", "emadev", "mom", "tflow")
CSV_FIELDS = (
    "frame",
    "timestamp_ns",
    "bid_idx",
    "bid_qty",
    "ask_idx",
    "ask_qty",
    *FEATURE_FIELDS,
)


class DatasetBuildError(RuntimeError):
    """Raised when input, parameters, or generated frames are invalid."""


@dataclass(frozen=True)
class BuildConfig:
    input_path: Path
    output_path: Path
    ticker: str
    base_price: int
    window_size: int
    stock_locate: int | None = None
    qty_shift: int = 0
    table_bits: int = 14
    metadata_path: Path | None = None
    limit: int | None = None
    force: bool = False


def _validate_config(config: BuildConfig) -> None:
    input_path = config.input_path.resolve()
    output_path = config.output_path.resolve()
    metadata_path = (
        config.metadata_path or config.output_path.with_suffix(".meta.json")
    ).resolve()

    if not input_path.is_file():
        raise DatasetBuildError(f"input file does not exist: {config.input_path}")
    if input_path in (output_path, metadata_path):
        raise DatasetBuildError("input, output, and metadata paths must differ")
    if output_path == metadata_path:
        raise DatasetBuildError("CSV output and metadata paths must differ")
    if config.output_path.suffix.lower() != ".csv":
        raise DatasetBuildError("output path must use the .csv extension")

    ticker = config.ticker.strip().upper()
    if not ticker or len(ticker) > 8:
        raise DatasetBuildError("ticker must contain 1 to 8 ASCII characters")
    try:
        ticker.encode("ascii")
    except UnicodeEncodeError as exc:
        raise DatasetBuildError("ticker must contain only ASCII characters") from exc

    if config.stock_locate is not None and not 0 <= config.stock_locate <= 0xFFFF:
        raise DatasetBuildError("stock_locate must be in [0, 65535]")
    if config.base_price < 0 or config.base_price % TICK_SIZE != 0:
        raise DatasetBuildError(
            f"base_price must be non-negative and aligned to {TICK_SIZE}"
        )
    if config.window_size <= 0 or config.window_size & (config.window_size - 1):
        raise DatasetBuildError("window_size must be a positive power of two")
    if not 0 <= config.qty_shift <= 31:
        raise DatasetBuildError("qty_shift must be in [0, 31]")
    if not 1 <= config.table_bits <= 20:
        raise DatasetBuildError("table_bits must be in [1, 20]")
    if config.limit is not None and config.limit <= 0:
        raise DatasetBuildError("limit must be positive when provided")

    if not config.force:
        for path in (output_path, metadata_path):
            if path.exists():
                raise DatasetBuildError(
                    f"output already exists: {path}; pass --force to replace it"
                )


def _resolve_stock_locate(input_path: Path, ticker: str) -> int:
    target = ticker.strip().upper().encode("ascii")
    for body in iter_messages(input_path):
        if body[0] != MT_STOCK_DIR:
            continue
        symbol = body[11:19].rstrip(b" ")
        if symbol == target:
            return locate_of(body)
    raise DatasetBuildError(
        f"ticker {ticker.upper()} was not found in any Stock Directory ('R') message"
    )


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def _validate_frame(frame: dict[str, int], previous_timestamp: int | None) -> None:
    timestamp = frame["timestamp_ns"]
    if previous_timestamp is not None and timestamp < previous_timestamp:
        raise DatasetBuildError(
            "feature timestamps are not monotonic: "
            f"{timestamp} followed {previous_timestamp}"
        )
    for field in FEATURE_FIELDS:
        value = frame[field]
        if not -32768 <= value <= 32767:
            raise DatasetBuildError(
                f"feature {field}={value} is outside signed int16 range"
            )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, indent=2, sort_keys=True)
        file.write("\n")


def build_dataset(config: BuildConfig) -> dict[str, Any]:
    """Build one feature CSV and return the metadata written beside it."""

    _validate_config(config)

    ticker = config.ticker.strip().upper()
    input_path = config.input_path.resolve()
    output_path = config.output_path.resolve()
    metadata_path = (
        config.metadata_path or config.output_path.with_suffix(".meta.json")
    ).resolve()

    resolved_locate = _resolve_stock_locate(input_path, ticker)
    if (
        config.stock_locate is not None
        and config.stock_locate != resolved_locate
    ):
        raise DatasetBuildError(
            f"stock_locate mismatch for {ticker}: "
            f"file says {resolved_locate}, argument says {config.stock_locate}"
        )
    stock_locate = resolved_locate

    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    output_tmp = output_path.with_name(f".{output_path.name}.tmp")
    metadata_tmp = metadata_path.with_name(f".{metadata_path.name}.tmp")

    golden = Golden(
        config.base_price,
        config.window_size,
        qty_shift=config.qty_shift,
        table_bits=config.table_bits,
    )

    scanned_messages = 0
    locate_messages = 0
    book_events = 0
    frame_count = 0
    first_timestamp: int | None = None
    previous_timestamp: int | None = None

    try:
        with output_tmp.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
            writer.writeheader()

            for body in iter_messages(input_path, limit=config.limit):
                scanned_messages += 1
                if locate_of(body) != stock_locate:
                    continue
                locate_messages += 1
                if body[0] not in BOOK_TYPES:
                    continue

                event = parse_event(body)
                if event is None:
                    continue
                book_events += 1
                golden.apply(event)

                for frame in golden.frames:
                    _validate_frame(frame, previous_timestamp)
                    timestamp = frame["timestamp_ns"]
                    if first_timestamp is None:
                        first_timestamp = timestamp
                    previous_timestamp = timestamp

                    row = {"frame": frame_count}
                    row.update({field: frame[field] for field in CSV_FIELDS[1:]})
                    writer.writerow(row)
                    frame_count += 1

                # Golden state is stored separately; emitted frame dicts are not
                # needed after writing. Clearing prevents day-scale memory growth.
                golden.frames.clear()

        if frame_count == 0:
            raise DatasetBuildError(
                "no feature frames were generated; check locate/base/window"
            )

        metadata: dict[str, Any] = {
            "schema_version": 1,
            "ticker": ticker,
            "stock_locate": stock_locate,
            "base_price": config.base_price,
            "window_size": config.window_size,
            "qty_shift": config.qty_shift,
            "table_bits": config.table_bits,
            "handler_commit": _git_commit(),
            "input_file": input_path.name,
            "input_size_bytes": input_path.stat().st_size,
            "columns": list(CSV_FIELDS),
            "counts": {
                "scanned_messages": scanned_messages,
                "locate_messages": locate_messages,
                "book_events": book_events,
                "feature_frames": frame_count,
                "out_of_window": golden.oow,
                "lookup_misses": golden.miss,
            },
            "timestamp_ns": {
                "first": first_timestamp,
                "last": previous_timestamp,
            },
        }
        _write_json(metadata_tmp, metadata)

        output_tmp.replace(output_path)
        metadata_tmp.replace(metadata_path)
        return metadata
    finally:
        output_tmp.unlink(missing_ok=True)
        metadata_tmp.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a timestamped feature CSV with the active ITCH50 handler "
            "Golden model. Run itch_tools.py calibrate first to obtain "
            "base-price and window-size."
        )
    )
    parser.add_argument("--input", dest="input_path", type=Path, required=True)
    parser.add_argument("--output", dest="output_path", type=Path, required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument(
        "--locate",
        dest="stock_locate",
        type=int,
        default=None,
        help="optional expected stock_locate; verified against the file",
    )
    parser.add_argument("--base-price", type=int, required=True)
    parser.add_argument("--window-size", type=int, required=True)
    parser.add_argument("--qty-shift", type=int, default=0)
    parser.add_argument("--table-bits", type=int, default=14)
    parser.add_argument(
        "--metadata",
        dest="metadata_path",
        type=Path,
        default=None,
        help="metadata JSON path; defaults to <output>.meta.json",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="stop after this many raw ITCH messages",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace existing CSV and metadata outputs",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = BuildConfig(
        input_path=args.input_path,
        output_path=args.output_path,
        ticker=args.ticker,
        stock_locate=args.stock_locate,
        base_price=args.base_price,
        window_size=args.window_size,
        qty_shift=args.qty_shift,
        table_bits=args.table_bits,
        metadata_path=args.metadata_path,
        limit=args.limit,
        force=args.force,
    )
    try:
        metadata = build_dataset(config)
    except DatasetBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    counts = metadata["counts"]
    print(
        f"wrote {counts['feature_frames']} feature frames "
        f"for {metadata['ticker']} (locate {metadata['stock_locate']})"
    )
    print(f"  CSV:  {config.output_path.resolve()}")
    meta_path = config.metadata_path or config.output_path.with_suffix(".meta.json")
    print(f"  meta: {meta_path.resolve()}")
    print(
        f"  oow={counts['out_of_window']} "
        f"misses={counts['lookup_misses']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
