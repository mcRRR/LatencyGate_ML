"""Python reference for Feature Vector v0."""

from collections import deque
from dataclasses import dataclass
from enum import IntEnum


PRICE_SCALE = 100
INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1


class MsgType(IntEnum):
    ADD = 0x01
    CANCEL = 0x02
    EXECUTE = 0x03


class Side(IntEnum):
    BID = 0x00
    ASK = 0x01


@dataclass(frozen=True)
class Message:
    """FM24-like event used by the reference model."""

    msg_type: MsgType
    side: Side
    price: int
    qty: int
    seq: int


@dataclass(frozen=True)
class TopOfBook:
    bid: int
    bid_qty: int
    ask: int
    ask_qty: int


@dataclass(frozen=True)
class FeatureVector:
    """Board-link Feature Vector v0 order."""

    spread: int
    tob_imbalance: int
    ofi: int
    ema_deviation: int
    momentum_8: int
    trade_flow_16: int

    def as_list(self) -> list[int]:
        return [
            self.spread,
            self.tob_imbalance,
            self.ofi,
            self.ema_deviation,
            self.momentum_8,
            self.trade_flow_16,
        ]


class ReferenceFeatureExtractor:
    """Simple price-level book plus v0 feature extraction."""

    def __init__(self) -> None:
        self.bid_book: dict[int, int] = {}
        self.ask_book: dict[int, int] = {}
        self.prev_tob: TopOfBook | None = None
        self.ema_mid: int | None = None
        self.mid_history: deque[int] = deque(maxlen=9)
        self.trade_flow_window: deque[int] = deque(maxlen=16)
        self.last_seq: int | None = None

    def update(self, msg: Message) -> FeatureVector | None:
        """Apply one event, then return a feature vector if TOB is valid."""

        self._validate_msg(msg)

        if msg.msg_type == MsgType.ADD:
            self._add(msg.side, msg.price, msg.qty)
        elif msg.msg_type == MsgType.CANCEL:
            self._remove(msg.side, msg.price, msg.qty)
        elif msg.msg_type == MsgType.EXECUTE:
            self._record_trade_flow(msg.side, msg.qty)
            self._remove(msg.side, msg.price, msg.qty)
        else:
            raise ValueError(f"Unsupported message type: {msg.msg_type}")

        self.last_seq = msg.seq

        tob = self.top_of_book()
        if tob is None:
            return None

        return self._features_from_tob(tob)

    def top_of_book(self) -> TopOfBook | None:
        """Return best bid/ask after empty levels have been removed."""

        if not self.bid_book or not self.ask_book:
            return None

        best_bid = max(self.bid_book)
        best_ask = min(self.ask_book)

        return TopOfBook(
            bid=best_bid,
            bid_qty=self.bid_book[best_bid],
            ask=best_ask,
            ask_qty=self.ask_book[best_ask],
        )

    def _features_from_tob(self, tob: TopOfBook) -> FeatureVector:
        spread = tob.ask - tob.bid
        tob_imbalance = tob.bid_qty - tob.ask_qty
        mid = (tob.bid + tob.ask) >> 1

        ofi = self._compute_ofi(tob)
        ema_deviation = self._compute_ema_deviation(mid)
        momentum_8 = self._compute_momentum_8(mid)
        trade_flow_16 = sum(self.trade_flow_window)

        self.prev_tob = tob

        return FeatureVector(
            spread=self._saturate_int32(spread),
            tob_imbalance=self._saturate_int32(tob_imbalance),
            ofi=self._saturate_int32(ofi),
            ema_deviation=self._saturate_int32(ema_deviation),
            momentum_8=self._saturate_int32(momentum_8),
            trade_flow_16=self._saturate_int32(trade_flow_16),
        )

    def _compute_ofi(self, tob: TopOfBook) -> int:
        if self.prev_tob is None:
            return 0

        delta_bid_qty = tob.bid_qty - self.prev_tob.bid_qty
        delta_ask_qty = tob.ask_qty - self.prev_tob.ask_qty
        return delta_bid_qty - delta_ask_qty

    def _compute_ema_deviation(self, mid: int) -> int:
        if self.ema_mid is None:
            self.ema_mid = mid

        # alpha = 1/16, matching the RTL shift implementation.
        self.ema_mid = self.ema_mid + ((mid - self.ema_mid) >> 4)
        return mid - self.ema_mid

    def _compute_momentum_8(self, mid: int) -> int:
        self.mid_history.append(mid)
        base_mid = self.mid_history[0]
        return mid - base_mid

    def _record_trade_flow(self, side: Side, qty: int) -> None:
        # EXECUTE on ASK means active buy; EXECUTE on BID means active sell.
        if side == Side.ASK:
            self.trade_flow_window.append(qty)
        elif side == Side.BID:
            self.trade_flow_window.append(-qty)
        else:
            raise ValueError(f"Unsupported side: {side}")

    def _add(self, side: Side, price: int, qty: int) -> None:
        book = self._book_for_side(side)
        book[price] = book.get(price, 0) + qty

    def _remove(self, side: Side, price: int, qty: int) -> None:
        book = self._book_for_side(side)
        new_qty = max(book.get(price, 0) - qty, 0)

        if new_qty == 0:
            book.pop(price, None)
        else:
            book[price] = new_qty

    def _book_for_side(self, side: Side) -> dict[int, int]:
        if side == Side.BID:
            return self.bid_book
        if side == Side.ASK:
            return self.ask_book
        raise ValueError(f"Unsupported side: {side}")

    def _validate_msg(self, msg: Message) -> None:
        if msg.qty < 0:
            raise ValueError("qty must be non-negative")
        if msg.seq < 1:
            raise ValueError("seq must be >= 1")
        if self.last_seq is not None and msg.seq != self.last_seq + 1:
            raise ValueError(f"seq must increment by 1: got {msg.seq}")

    @staticmethod
    def _saturate_int32(value: int) -> int:
        return max(INT32_MIN, min(INT32_MAX, value))


if __name__ == "__main__":
    ext = ReferenceFeatureExtractor()
    messages = [
        Message(MsgType.ADD, Side.BID, price=10000, qty=5, seq=1),
        Message(MsgType.ADD, Side.ASK, price=10003, qty=7, seq=2),
        Message(MsgType.CANCEL, Side.BID, price=10000, qty=2, seq=3),
        Message(MsgType.EXECUTE, Side.ASK, price=10003, qty=3, seq=4),
        Message(MsgType.ADD, Side.ASK, price=10005, qty=4, seq=5),
        Message(MsgType.CANCEL, Side.ASK, price=10003, qty=4, seq=6),
    ]

    for msg in messages:
        print(msg, "->", ext.update(msg))
