from datetime import datetime, timedelta

import pytest

from app.data.models import MarketTick
from app.state.manager import StateManager


def make_tick(
    symbol: str,
    price: float,
    timestamp: datetime,
    volume: int = 1_000,
) -> MarketTick:
    return MarketTick(
        symbol=symbol,
        price=price,
        timestamp=timestamp,
        volume=volume,
    )


def test_state_manager_stores_tick():
    manager = StateManager(window_minutes=60)

    timestamp = datetime(2026, 9, 2, 10, 0)

    tick = make_tick(
        symbol="RELIANCE",
        price=100.0,
        timestamp=timestamp,
    )

    manager.update(tick)

    history = manager.get_history("RELIANCE")

    assert len(history) == 1
    assert history[0].price == 100.0
    assert history[0].timestamp == timestamp


def test_state_manager_keeps_stocks_separate():
    manager = StateManager(window_minutes=60)

    timestamp = datetime(2026, 9, 2, 10, 0)

    manager.update(
        make_tick("RELIANCE", 100.0, timestamp)
    )

    manager.update(
        make_tick("TCS", 200.0, timestamp)
    )

    assert manager.get_stock_count() == 2

    assert manager.get_latest("RELIANCE").price == 100.0
    assert manager.get_latest("TCS").price == 200.0


def test_state_manager_keeps_recent_history():
    manager = StateManager(window_minutes=60)

    base_time = datetime(2026, 9, 2, 10, 0)

    manager.update(
        make_tick("RELIANCE", 100.0, base_time)
    )

    manager.update(
        make_tick(
            "RELIANCE",
            110.0,
            base_time + timedelta(minutes=30),
        )
    )

    manager.update(
        make_tick(
            "RELIANCE",
            120.0,
            base_time + timedelta(minutes=60),
        )
    )

    history = manager.get_history("RELIANCE")

    assert len(history) == 3


def test_state_manager_removes_old_observations():
    manager = StateManager(window_minutes=60)

    base_time = datetime(2026, 9, 2, 10, 0)

    manager.update(
        make_tick("RELIANCE", 100.0, base_time)
    )

    manager.update(
        make_tick(
            "RELIANCE",
            110.0,
            base_time + timedelta(minutes=30),
        )
    )

    manager.update(
        make_tick(
            "RELIANCE",
            120.0,
            base_time + timedelta(minutes=61),
        )
    )

    history = manager.get_history("RELIANCE")

    assert len(history) == 2

    assert history[0].timestamp == base_time + timedelta(minutes=30)


def test_state_manager_returns_latest_observation():
    manager = StateManager(window_minutes=60)

    base_time = datetime(2026, 9, 2, 10, 0)

    manager.update(
        make_tick("RELIANCE", 100.0, base_time)
    )

    manager.update(
        make_tick(
            "RELIANCE",
            105.0,
            base_time + timedelta(minutes=5),
        )
    )

    latest = manager.get_latest("RELIANCE")

    assert latest is not None
    assert latest.price == 105.0


def test_state_manager_unknown_stock_returns_empty_history():
    manager = StateManager(window_minutes=60)

    assert manager.get_history("UNKNOWN") == []
    assert manager.get_latest("UNKNOWN") is None
    assert manager.get_observation_count("UNKNOWN") == 0


def test_state_manager_rejects_invalid_price():
    manager = StateManager(window_minutes=60)

    tick = make_tick(
        symbol="RELIANCE",
        price=0,
        timestamp=datetime(2026, 9, 2, 10, 0),
    )

    with pytest.raises(ValueError):
        manager.update(tick)


def test_state_manager_rejects_invalid_window():
    with pytest.raises(ValueError):
        StateManager(window_minutes=0)


def test_state_manager_clear():
    manager = StateManager(window_minutes=60)

    manager.update(
        make_tick(
            "RELIANCE",
            100.0,
            datetime(2026, 9, 2, 10, 0),
        )
    )

    assert manager.get_stock_count() == 1

    manager.clear()

    assert manager.get_stock_count() == 0