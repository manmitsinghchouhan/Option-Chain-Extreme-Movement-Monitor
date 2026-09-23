from datetime import datetime

from app.alerts.manager import AlertManager
from app.detection.detector import (
    ExtremeEvent,
    MovementDirection,
)


def make_event(
    symbol: str = "RELIANCE",
    threshold: float = 60.0,
    direction: MovementDirection = MovementDirection.DOWN,
) -> ExtremeEvent:
    return ExtremeEvent(
        symbol=symbol,
        direction=direction,
        threshold=threshold,
        percentage_change=-threshold,
        start_price=100.0,
        current_price=100.0 - threshold,
        start_timestamp=datetime(2026, 9, 2, 10, 0),
        current_timestamp=datetime(2026, 9, 2, 11, 0),
        duration_seconds=3600,
    )


def test_first_event_is_new():
    manager = AlertManager()

    event = make_event()

    assert manager.process(event) is True


def test_duplicate_event_is_rejected():
    manager = AlertManager()

    event = make_event()

    assert manager.process(event) is True
    assert manager.process(event) is False


def test_different_thresholds_are_independent():
    manager = AlertManager()

    event_60 = make_event(threshold=60.0)
    event_70 = make_event(threshold=70.0)
    event_80 = make_event(threshold=80.0)

    assert manager.process(event_60) is True
    assert manager.process(event_70) is True
    assert manager.process(event_80) is True

    assert manager.active_count() == 3


def test_up_and_down_are_independent():
    manager = AlertManager()

    down_event = make_event(
        direction=MovementDirection.DOWN
    )

    up_event = make_event(
        direction=MovementDirection.UP
    )

    assert manager.process(down_event) is True
    assert manager.process(up_event) is True


def test_threshold_can_be_reactivated():
    manager = AlertManager()

    event = make_event()

    assert manager.process(event) is True
    assert manager.process(event) is False

    manager.reset_below_threshold(
        symbol="RELIANCE",
        direction="DOWN",
        threshold=60.0,
    )

    assert manager.process(event) is True


def test_clear_removes_all_active_events():
    manager = AlertManager()

    manager.process(make_event(threshold=60.0))
    manager.process(make_event(threshold=70.0))

    assert manager.active_count() == 2

    manager.clear()

    assert manager.active_count() == 0


def test_sync_resets_automatically_reactivates_threshold():
    manager = AlertManager()
    event_60 = make_event(threshold=60.0, direction=MovementDirection.UP)

    # 1. Price jumps to +65% -> Triggers 60% UP alert
    assert manager.process(event_60) is True
    # Duplicate tick while still at +65% -> No repeat alert
    assert manager.process(event_60) is False

    # 2. Price cools down back to +45% (below 60%)
    manager.sync_resets(
        instrument_key="RELIANCE",
        percentage_change=45.0,
        thresholds=(60.0, 70.0, 80.0),
    )

    # 3. Price surges again to +65% -> Triggers NEW alert!
    assert manager.process(event_60) is True