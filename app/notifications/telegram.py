import os

import requests

from app.detection.detector import ExtremeEvent


class TelegramNotifier:
    """Sends extreme movement notifications through Telegram."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

    def is_configured(self) -> bool:
        """Return whether Telegram credentials are available."""

        return bool(self.bot_token and self.chat_id)

    def format_message(self, event: ExtremeEvent) -> str:
        """Create a human-readable Telegram message for stock or option alerts."""

        direction_symbol = "🟢" if event.direction.value == "UP" else "🔴"
        duration_minutes = event.duration_seconds / 60

        if event.is_option and event.option_type is not None:
            title = f"🚨 EXTREME OPTION MOVEMENT DETECTED"
            contract_info = (
                f"📊 Contract: {event.symbol} {event.strike_price:.0f} {event.option_type.value}\n"
                f"🏷️ Type: {'CALL (CE)' if event.option_type.value == 'CE' else 'PUT (PE)'}\n"
                f"🎯 Strike: ₹{event.strike_price:.0f} | Expiry: {event.expiry}\n"
                f"💰 Start Premium: ₹{event.start_price:.2f} ➔ Current: ₹{event.current_price:.2f}\n"
            )
            if event.underlying_price > 0:
                contract_info += f"📈 Underlying Spot: ₹{event.underlying_price:.2f}\n"
        else:
            title = f"🚨 EXTREME MOVEMENT DETECTED"
            contract_info = (
                f"📊 Stock: {event.symbol}\n"
                f"💰 Start Price: ₹{event.start_price:.2f} ➔ Current: ₹{event.current_price:.2f}\n"
            )


        return (
            f"{title}\n\n"
            f"{direction_symbol} Stock: {event.symbol}\n"
            f"Movement: {event.percentage_change:+.2f}%\n"
            f"Direction: {event.direction.value}\n"
            f"Threshold: {event.threshold:.0f}%\n"
            f"{contract_info}"
            f"Duration: {duration_minutes:.1f} minutes\n"
            f"Time: {event.current_timestamp:%H:%M:%S}"
        )



    def send(self, event: ExtremeEvent) -> bool:
        """
        Send an event to Telegram.

        Returns True if Telegram accepted the message.
        """

        if not self.is_configured():
            raise RuntimeError(
                "Telegram is not configured. "
                "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
            )

        url = (
            f"https://api.telegram.org/bot"
            f"{self.bot_token}/sendMessage"
        )

        response = requests.post(
            url,
            json={
                "chat_id": self.chat_id,
                "text": self.format_message(event),
            },
            timeout=10,
        )

        response.raise_for_status()

        return bool(response.json().get("ok"))

    def send_test_message(self) -> bool:
        """Send a test ping message to verify Telegram bot setup."""
        if not self.is_configured():
            raise RuntimeError("Telegram credentials missing in .env")

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        response = requests.post(
            url,
            json={
                "chat_id": self.chat_id,
                "text": (
                    "⚡ <b>F&O Extreme Movement Monitor</b>\n\n"
                    "✅ <b>Telegram Bot Connected Successfully!</b>\n"
                    "Your system is configured to receive real-time alerts for "
                    "-60%, -70%, and -80% option premium crashes."
                ),
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        response.raise_for_status()
        return bool(response.json().get("ok"))