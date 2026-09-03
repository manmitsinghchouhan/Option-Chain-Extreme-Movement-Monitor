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

    assert "EXTREME MOVEMENT DETECTED" in message
    assert "RELIANCE" in message
    assert "-80.00%" in message
    assert "DOWN" in message
    assert "80%" in message
    assert "₹20.00" in message
    assert "55.0 minutes" in message