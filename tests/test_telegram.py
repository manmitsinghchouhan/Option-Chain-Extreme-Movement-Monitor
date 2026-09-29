from datetime import datetime

from app.detection.detector import (
    ExtremeEvent,
    MovementDirection,
)
from app.notifications.telegram import TelegramNotifier


def make_event():
    return ExtremeEvent(
        symbol="RELIANCE",
        direction=MovementDirection.DOWN,
        threshold=80.0,
        percentage_change=-80.0,
        start_price=100.0,
        current_price=20.0,
        start_timestamp=datetime(2026, 9, 2, 10, 0),
        current_timestamp=datetime(2026, 9, 2, 10, 55),
        duration_seconds=55 * 60,
    )


def test_telegram_message_contains_event_information():
    notifier = TelegramNotifier(
        bot_token="test-token",
        chat_id="test-chat",
    )

    message = notifier.format_message(make_event())

    assert "CRITICAL CRASH -80%" in message
    assert "EXTREME MOVEMENT DETECTED" in message
    assert "RELIANCE" in message
    assert "-80.00%" in message
    assert "DOWN" in message
    assert "80%" in message
    assert "₹20.00" in message
    assert "55.0 minutes" in message


def test_telegram_penny_decay_formatting_and_routing():
    from app.data.models import OptionType

    penny_event = ExtremeEvent(
        symbol="UPL",
        direction=MovementDirection.DOWN,
        threshold=70.0,
        percentage_change=-75.0,
        start_price=0.20,
        current_price=0.05,
        start_timestamp=datetime(2026, 9, 2, 10, 0),
        current_timestamp=datetime(2026, 9, 2, 10, 30),
        duration_seconds=30 * 60,
        strike_price=610.0,
        option_type=OptionType.CE,
        expiry="2026-09-29",
    )

    assert penny_event.is_penny_decay is True

    notifier = TelegramNotifier(
        bot_token="core-token",
        chat_id="core-chat",
        penny_bot_token="penny-token",
        penny_chat_id="penny-chat",
    )

    msg = notifier.format_message(penny_event)
    assert "HIGH ALERT -70%" in msg
    assert "PENNY / EXPIRY DECAY" in msg
    assert "UPL 610 CE" in msg
    assert "Start Premium: ₹0.20" in msg
    assert "Current: ₹0.05" in msg
    assert notifier.is_configured() is True
    assert notifier.is_penny_configured() is True


def test_telegram_tiered_severity_headers():
    from app.data.models import OptionType

    notifier = TelegramNotifier(bot_token="t", chat_id="c")

    # 60% event
    e60 = ExtremeEvent(
        symbol="TATA",
        direction=MovementDirection.DOWN,
        threshold=60.0,
        percentage_change=-62.0,
        start_price=10.0,
        current_price=3.8,
        start_timestamp=datetime.now(),
        current_timestamp=datetime.now(),
        duration_seconds=600,
        strike_price=100.0,
        option_type=OptionType.CE,
    )
    msg60 = notifier.format_message(e60)
    assert "TRIGGER -60%" in msg60
    assert "🟢" in msg60

    # 70% event
    e70 = ExtremeEvent(
        symbol="TATA",
        direction=MovementDirection.DOWN,
        threshold=70.0,
        percentage_change=-72.0,
        start_price=10.0,
        current_price=2.8,
        start_timestamp=datetime.now(),
        current_timestamp=datetime.now(),
        duration_seconds=600,
        strike_price=100.0,
        option_type=OptionType.CE,
    )
    msg70 = notifier.format_message(e70)
    assert "HIGH ALERT -70%" in msg70
    assert "🟡" in msg70

    # 80% event
    e80 = ExtremeEvent(
        symbol="TATA",
        direction=MovementDirection.DOWN,
        threshold=80.0,
        percentage_change=-82.0,
        start_price=10.0,
        current_price=1.8,
        start_timestamp=datetime.now(),
        current_timestamp=datetime.now(),
        duration_seconds=600,
        strike_price=100.0,
        option_type=OptionType.CE,
    )
    msg80 = notifier.format_message(e80)
    assert "CRITICAL CRASH -80%" in msg80
    assert "🔴" in msg80


def test_telegram_index_options_routing():
    from app.data.models import OptionType

    index_event = ExtremeEvent(
        symbol="NIFTY",
        direction=MovementDirection.DOWN,
        threshold=80.0,
        percentage_change=-85.0,
        start_price=150.0,
        current_price=22.5,
        start_timestamp=datetime(2026, 9, 2, 10, 0),
        current_timestamp=datetime(2026, 9, 2, 10, 45),
        duration_seconds=45 * 60,
        strike_price=25000.0,
        option_type=OptionType.PE,
        expiry="2026-10-01",
    )

    assert index_event.is_index is True
    assert index_event.is_penny_decay is False

    notifier = TelegramNotifier(
        bot_token="core-token",
        chat_id="core-chat",
        index_bot_token="index-token",
        index_chat_id="index-chat",
        penny_bot_token="penny-token",
        penny_chat_id="penny-chat",
    )

    msg = notifier.format_message(index_event)
    assert "CRITICAL CRASH -80%" in msg
    assert "EXTREME INDEX MOVEMENT DETECTED" in msg
    assert "NIFTY 25000 PE" in msg
    assert notifier.is_index_configured() is True
    assert notifier.is_configured() is True
    assert notifier.is_penny_configured() is True