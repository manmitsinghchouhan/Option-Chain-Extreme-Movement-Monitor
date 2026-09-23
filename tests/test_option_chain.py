from datetime import datetime, timedelta

from app.alerts.manager import AlertManager
from app.data.dummy import DummyMarketDataProvider
from app.data.models import OptionTick, OptionType
from app.detection.detector import ExtremeDetector, MovementDirection
from app.movement.calculator import MovementResult, calculate_movement
from app.state.manager import PriceObservation, StateManager


def test_option_tick_properties():
    tick = OptionTick(
        symbol="TCS",
        strike_price=4000.0,
        option_type=OptionType.CE,
        expiry="2026-09-24",
        premium=45.0,
        timestamp=datetime.now(),
        volume=5000,
        open_interest=25000,
        underlying_price=3980.0,
    )

    assert tick.symbol == "TCS"
    assert tick.strike_price == 4000.0
    assert tick.option_type == OptionType.CE
    assert tick.instrument_key == "TCS_2026-09-24_4000_CE"
    assert tick.display_name == "TCS 4000 CE"
    assert tick.price == 45.0


def test_state_manager_stores_option_contracts():
    manager = StateManager(window_minutes=60)
    now = datetime.now()

    ce_tick = OptionTick(
        symbol="RELIANCE",
        strike_price=2500.0,
        option_type=OptionType.CE,
        expiry="2026-09-24",
        premium=50.0,
        timestamp=now,
        volume=1000,
        underlying_price=2490.0,
    )

    pe_tick = OptionTick(
        symbol="RELIANCE",
        strike_price=2500.0,
        option_type=OptionType.PE,
        expiry="2026-09-24",
        premium=30.0,
        timestamp=now,
        volume=800,
        underlying_price=2490.0,
    )

    manager.update(ce_tick)
    manager.update(pe_tick)

    assert manager.get_instrument_count() == 2
    assert len(manager.get_instruments_for_symbol("RELIANCE")) == 2

    latest_ce = manager.get_latest("RELIANCE_2026-09-24_2500_CE")
    assert latest_ce is not None
    assert latest_ce.price == 50.0

    latest_pe = manager.get_latest("RELIANCE_2026-09-24_2500_PE")
    assert latest_pe is not None
    assert latest_pe.price == 30.0


def test_option_extreme_detection_up_and_down():
    detector = ExtremeDetector(thresholds=(60.0, 70.0, 80.0), allow_up=True)
    now = datetime.now()

    # Call Option surge: 20.0 -> 35.0 (+75%)
    up_movement = MovementResult(
        start_price=20.0,
        current_price=35.0,
        percentage_change=75.0,
        start_timestamp=now - timedelta(minutes=10),
        current_timestamp=now,
        duration_seconds=600,
    )

    up_events = detector.detect(
        symbol="TCS",
        movement=up_movement,
        strike_price=4000.0,
        option_type=OptionType.CE,
        expiry="2026-09-24",
    )

    assert len(up_events) == 2  # Crosses 60% and 70%
    assert all(e.direction == MovementDirection.UP for e in up_events)
    assert up_events[0].display_title == "TCS 4000 CE"
    assert up_events[0].threshold == 60.0
    assert up_events[1].threshold == 70.0

    # Put Option plunge: 50.0 -> 10.0 (-80%)
    down_movement = MovementResult(
        start_price=50.0,
        current_price=10.0,
        percentage_change=-80.0,
        start_timestamp=now - timedelta(minutes=15),
        current_timestamp=now,
        duration_seconds=900,
    )

    down_events = detector.detect(
        symbol="INFY",
        movement=down_movement,
        strike_price=1600.0,
        option_type=OptionType.PE,
        expiry="2026-09-24",
    )

    assert len(down_events) == 3  # Crosses 60%, 70%, 80%
    assert all(e.direction == MovementDirection.DOWN for e in down_events)
    assert down_events[0].display_title == "INFY 1600 PE"


def test_alert_manager_deduplicates_by_instrument():
    detector = ExtremeDetector(thresholds=(60.0, 70.0, 80.0), allow_up=True)
    alert_mgr = AlertManager()
    now = datetime.now()

    mov_ce = MovementResult(
        start_price=20.0,
        current_price=34.0,
        percentage_change=70.0,
        start_timestamp=now,
        current_timestamp=now,
        duration_seconds=100,
    )

    events_ce = detector.detect(
        symbol="TCS",
        movement=mov_ce,
        strike_price=4000.0,
        option_type=OptionType.CE,
        expiry="2026-09-24",
    )

    # First time processing events -> should trigger alerts
    assert alert_mgr.process(events_ce[0]) is True
    assert alert_mgr.process(events_ce[1]) is True

    # Duplicate call -> should return False (no spam)
    assert alert_mgr.process(events_ce[0]) is False
    assert alert_mgr.process(events_ce[1]) is False

    # A different strike (4050 CE) should trigger its own new alert!
    events_ce_4050 = detector.detect(
        symbol="TCS",
        movement=mov_ce,
        strike_price=4050.0,
        option_type=OptionType.CE,
        expiry="2026-09-24",
    )

    assert alert_mgr.process(events_ce_4050[0]) is True
