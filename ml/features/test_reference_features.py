from reference_features import (
    FeatureVector,
    Message,
    MsgType,
    ReferenceFeatureExtractor,
    Side,
)


def test_feature_vector_v0_sequence() -> None:
    ext = ReferenceFeatureExtractor()
    messages = [
        Message(MsgType.ADD, Side.BID, price=10000, qty=5, seq=1),
        Message(MsgType.ADD, Side.ASK, price=10003, qty=7, seq=2),
        Message(MsgType.CANCEL, Side.BID, price=10000, qty=2, seq=3),
        Message(MsgType.EXECUTE, Side.ASK, price=10003, qty=3, seq=4),
        Message(MsgType.ADD, Side.ASK, price=10005, qty=4, seq=5),
        Message(MsgType.CANCEL, Side.ASK, price=10003, qty=4, seq=6),
    ]

    outputs = [ext.update(msg) for msg in messages]

    assert outputs == [
        None,
        FeatureVector(3, -2, 0, 0, 0, 0),
        FeatureVector(3, -4, -2, 0, 0, 0),
        FeatureVector(3, -1, 3, 0, 0, 3),
        FeatureVector(3, -1, 0, 0, 0, 3),
        FeatureVector(5, -1, 0, 1, 1, 3),
    ]


def test_execute_bid_records_active_sell_flow() -> None:
    ext = ReferenceFeatureExtractor()
    messages = [
        Message(MsgType.ADD, Side.BID, price=10000, qty=10, seq=1),
        Message(MsgType.ADD, Side.ASK, price=10003, qty=7, seq=2),
        Message(MsgType.EXECUTE, Side.BID, price=10000, qty=4, seq=3),
    ]

    outputs = [ext.update(msg) for msg in messages]

    assert outputs[-1] == FeatureVector(3, -1, -4, 0, 0, -4)


def test_bad_sequence_raises() -> None:
    ext = ReferenceFeatureExtractor()
    ext.update(Message(MsgType.ADD, Side.BID, price=10000, qty=1, seq=1))

    try:
        ext.update(Message(MsgType.ADD, Side.ASK, price=10003, qty=1, seq=3))
    except ValueError as exc:
        assert "seq must increment" in str(exc)
    else:
        raise AssertionError("Expected bad seq to raise ValueError")


if __name__ == "__main__":
    test_feature_vector_v0_sequence()
    test_execute_bid_records_active_sell_flow()
    test_bad_sequence_raises()
    print("reference feature tests passed")
