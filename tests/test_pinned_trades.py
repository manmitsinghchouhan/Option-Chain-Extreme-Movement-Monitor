from datetime import datetime
from app.data.models import OptionType, PinnedTrade
from app.detection.detector import ExtremeEvent, MovementDirection
from app.engine import MonitorEngine, PINNED_FILE_PATH


def test_pinned_trade_serialization():
    trade = PinnedTrade(
        symbol="RELIANCE",
        strike_price=2900.0,
        option_type=OptionType.CE,
        expiry="2026-09-30",
        pinned_price=10.0,
        pinned_timestamp=datetime(2026, 9, 28, 10, 30),
        instrument_key="RELIANCE_2026-09-30_2900_CE",
        threshold=80.0,
        percentage_change=-80.0,
        underlying_price=2880.0,
    )
    data = trade.to_dict()
    assert data["symbol"] == "RELIANCE"
    assert data["pinned_price"] == 10.0
    assert data["option_type"] == "CE"
    restored = PinnedTrade.from_dict(data)
    assert restored.symbol == "RELIANCE"
    assert restored.strike_price == 2900.0
    assert restored.option_type == OptionType.CE
    assert restored.display_title == "RELIANCE 2900 CE"


def test_engine_pin_unpin_and_persistence():
    engine = MonitorEngine.get_instance()
    engine.clear_pinned_trades()
    assert len(engine.pinned_trades_list) == 0
    event = ExtremeEvent(
        symbol="INFY",
        direction=MovementDirection.DOWN,
        threshold=70.0,
        percentage_change=-70.0,
        start_price=20.0,
        current_price=6.0,
        start_timestamp=datetime.now(),
        current_timestamp=datetime.now(),
        duration_seconds=900,
        strike_price=1800.0,
        option_type=OptionType.PE,
        expiry="2026-09-30",
        instrument_key="INFY_2026-09-30_1800_PE",
        underlying_price=1780.0,
    )
    pinned = engine.pin_trade(event)
    assert pinned.pinned_price == 6.0
    assert len(engine.pinned_trades_list) == 1
    assert engine.pinned_trades_list[0].symbol == "INFY"
    assert PINNED_FILE_PATH.exists()
    assert engine.unpin_trade("INFY_2026-09-30_1800_PE") is True
    assert len(engine.pinned_trades_list) == 0
    engine.clear_pinned_trades()
