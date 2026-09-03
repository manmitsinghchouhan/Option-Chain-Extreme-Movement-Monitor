from datetime import datetime, timedelta

from app.alerts.manager import AlertManager
from app.data.dummy import MarketTick
from app.detection.detector import ExtremeDetector
from app.movement.calculator import calculate_movement
from app.state.manager import StateManager


def test_extreme_movement_pipeline():
    state = StateManager(window_minutes=60)
    detector = ExtremeDetector()
    alerts = AlertManager()

    base_time = datetime(2026, 9, 2, 10, 0)

    prices = [100.0, 95.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0]

    for index, price in enumerate(prices):
        tick = MarketTick(
            symbol="RELIANCE",
            price=price,
            timestamp=base_time + timedelta(minutes=index * 5),
            volume=10_000 + index,
        )

        state.update(tick)

    history = state.get_history("RELIANCE")

    movement = calculate_movement(history)

    assert movement is not None
    assert movement.percentage_change == -80.0

    events = detector.detect(
        "RELIANCE",
        movement,
    )

    assert len(events) == 3

    new_alerts = [
        event
        for event in events
        if alerts.process(event)
    ]

    assert len(new_alerts) == 3

    duplicate_alerts = [
        event
        for event in events
        if alerts.process(event)
    ]

    assert duplicate_alerts == []


def test_multiple_stocks_can_be_tracked():
    state = StateManager(window_minutes=60)

    timestamp = datetime(2026, 9, 2, 10, 0)

    stocks = {
        "RELIANCE": 100.0,
        "TCS": 200.0,
        "INFY": 300.0,
        "WIPRO": 400.0,
    }

    for symbol, price in stocks.items():
        state.update(
            MarketTick(
                symbol=symbol,
                price=price,
                timestamp=timestamp,
                volume=1_000,
            )
        )

    assert state.get_stock_count() == 4

    for symbol, price in stocks.items():
        assert state.get_latest(symbol).price == price