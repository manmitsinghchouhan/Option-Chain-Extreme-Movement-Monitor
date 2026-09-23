from datetime import datetime

import pytest

from app.detection.detector import (
    ExtremeDetector,
    MovementDirection,
)
from app.movement.calculator import MovementResult


def make_movement(percentage: float) -> MovementResult:
    return MovementResult(
        start_price=100.0,
        current_price=100.0 + percentage,
        percentage_change=percentage,
        start_timestamp=datetime(2026, 9, 2, 10, 0),
        current_timestamp=datetime(2026, 9, 2, 11, 0),
        duration_seconds=3600,
    )


def test_below_negative_60_percent_creates_no_event():
    detector = ExtremeDetector()

    events = detector.detect(
        "RELIANCE",
        make_movement(-59.9),
    )

    assert events == []


def test_exactly_negative_60_percent_creates_60_event():
    detector = ExtremeDetector()

    events = detector.detect(
        "RELIANCE",
        make_movement(-60.0),
    )

    assert len(events) == 1
    assert events[0].threshold == 60.0
    assert events[0].direction == MovementDirection.DOWN


def test_negative_80_percent_crosses_three_thresholds():
    detector = ExtremeDetector()

    events = detector.detect(
        "RELIANCE",
        make_movement(-80.0),
    )

    assert len(events) == 3
    assert [event.threshold for event in events] == [
        60.0,
        70.0,
        80.0,
    ]


def test_up_movement_ignored_by_default():
    detector = ExtremeDetector()

    events = detector.detect(
        "RELIANCE",
        make_movement(80.0),
    )

    assert events == []


def test_up_movement_detected_when_allow_up_enabled():
    detector = ExtremeDetector(allow_up=True)

    events = detector.detect(
        "RELIANCE",
        make_movement(80.0),
    )

    assert len(events) == 3
    assert events[0].direction == MovementDirection.UP


def test_custom_thresholds():
    detector = ExtremeDetector(
        thresholds=(50.0, 75.0)
    )

    events = detector.detect(
        "RELIANCE",
        make_movement(-80.0),
    )

    assert [event.threshold for event in events] == [
        50.0,
        75.0,
    ]


def test_invalid_threshold_is_rejected():
    with pytest.raises(ValueError):
        ExtremeDetector(thresholds=(0.0, 60.0))