"""Integration tests for the ITCH feature dataset builder."""

import csv
import json
import struct
import tempfile
import unittest
from pathlib import Path

from build_itch_dataset import BuildConfig, DatasetBuildError, build_dataset
from hardware.ax7a200b.src.itch_tools import _build_add, _build_exec


BASE_PRICE = 1_550_000
WINDOW_SIZE = 2048
LOCATE = 7


def stock_directory(ticker="AAPL", locate=LOCATE, timestamp_ns=10):
    body = bytearray(39)
    body[0] = ord("R")
    body[1:3] = struct.pack(">H", locate)
    body[5:11] = timestamp_ns.to_bytes(6, "big")
    body[11:19] = ticker.encode("ascii").ljust(8, b" ")
    return bytes(body)


def framed(*bodies):
    stream = bytearray()
    for body in bodies:
        stream += struct.pack(">H", len(body))
        stream += body
    return bytes(stream)


def sample_feed():
    return framed(
        stock_directory(),
        _build_add(
            100,
            "B",
            300,
            1_600_000,
            locate=LOCATE,
            timestamp_ns=100,
        ),
        _build_add(
            200,
            "S",
            200,
            1_600_200,
            locate=LOCATE,
            timestamp_ns=200,
        ),
        _build_exec(100, 50, locate=LOCATE, timestamp_ns=300),
    )


class DatasetBuilderTests(unittest.TestCase):
    def test_builds_csv_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "sample.itch"
            output_path = root / "features.csv"
            input_path.write_bytes(sample_feed())

            metadata = build_dataset(
                BuildConfig(
                    input_path=input_path,
                    output_path=output_path,
                    ticker="aapl",
                    base_price=BASE_PRICE,
                    window_size=WINDOW_SIZE,
                )
            )

            with output_path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            metadata_path = output_path.with_suffix(".meta.json")
            disk_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(len(rows), 2)
        self.assertEqual([int(row["timestamp_ns"]) for row in rows], [200, 300])
        self.assertEqual([int(row["frame"]) for row in rows], [0, 1])
        self.assertEqual(metadata["ticker"], "AAPL")
        self.assertEqual(metadata["stock_locate"], LOCATE)
        self.assertEqual(metadata["counts"]["feature_frames"], 2)
        self.assertEqual(metadata["counts"]["out_of_window"], 0)
        self.assertEqual(disk_metadata, metadata)

    def test_rejects_locate_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "sample.itch"
            input_path.write_bytes(sample_feed())

            with self.assertRaisesRegex(DatasetBuildError, "stock_locate mismatch"):
                build_dataset(
                    BuildConfig(
                        input_path=input_path,
                        output_path=root / "features.csv",
                        ticker="AAPL",
                        stock_locate=LOCATE + 1,
                        base_price=BASE_PRICE,
                        window_size=WINDOW_SIZE,
                    )
                )

    def test_rejects_non_monotonic_feature_timestamps(self):
        feed = framed(
            stock_directory(),
            _build_add(
                100,
                "B",
                300,
                1_600_000,
                locate=LOCATE,
                timestamp_ns=300,
            ),
            _build_add(
                200,
                "S",
                200,
                1_600_200,
                locate=LOCATE,
                timestamp_ns=200,
            ),
            _build_exec(100, 50, locate=LOCATE, timestamp_ns=100),
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "sample.itch"
            output_path = root / "features.csv"
            input_path.write_bytes(feed)

            with self.assertRaisesRegex(DatasetBuildError, "not monotonic"):
                build_dataset(
                    BuildConfig(
                        input_path=input_path,
                        output_path=output_path,
                        ticker="AAPL",
                        base_price=BASE_PRICE,
                        window_size=WINDOW_SIZE,
                    )
                )

            self.assertFalse(output_path.exists())
            self.assertFalse(output_path.with_suffix(".meta.json").exists())

    def test_rejects_non_power_of_two_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "sample.itch"
            input_path.write_bytes(sample_feed())

            with self.assertRaisesRegex(DatasetBuildError, "power of two"):
                build_dataset(
                    BuildConfig(
                        input_path=input_path,
                        output_path=root / "features.csv",
                        ticker="AAPL",
                        base_price=BASE_PRICE,
                        window_size=1000,
                    )
                )


if __name__ == "__main__":
    unittest.main()
