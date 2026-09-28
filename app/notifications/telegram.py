import logging
import os
import queue
import threading
import time

import requests

from app.detection.detector import ExtremeEvent

logger = logging.getLogger(__name__)


def _get_secret(*keys: str) -> str | None:
    """Retrieve secret matching any of the candidate keys from os.environ or streamlit secrets."""
    # 1. Check os.environ directly
    for key in keys:
        for k in (key, key.upper(), key.lower()):
            val = os.getenv(k)
            if val:
                return str(val).strip().strip('"').strip("'")

    # 2. Check streamlit secrets if available
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            for key in keys:
                for k in (key, key.upper(), key.lower()):
                    if k in st.secrets:
                        val = st.secrets[k]
                        if val:
                            return str(val).strip().strip('"').strip("'")

            for s_key in list(st.secrets.keys()):
                s_val = st.secrets[s_key]
                if isinstance(s_val, dict) or "secrets" in str(type(s_val)).lower():
                    for sub_k, sub_v in s_val.items():
                        for target in keys:
                            if target.lower() == sub_k.lower() or target.lower() in f"{s_key}_{sub_k}".lower():
                                if sub_v:
                                    return str(sub_v).strip().strip('"').strip("'")
                else:
                    for target in keys:
                        if target.lower() == s_key.lower():
                            if s_val:
                                return str(s_val).strip().strip('"').strip("'")
    except Exception:
        pass
    return None


class TelegramNotifier:
    """Sends extreme movement notifications through Telegram with dual-tier routing and background rate-limiting."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        penny_bot_token: str | None = None,
        penny_chat_id: str | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._penny_bot_token = penny_bot_token
        self._penny_chat_id = penny_chat_id
        self._msg_queue: queue.Queue = queue.Queue(maxsize=500)
        self._worker_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._worker_thread.start()

    @property
    def bot_token(self) -> str | None:
        """Main bot token for core high-value alerts (>= ₹1.00)."""
        return self._bot_token or _get_secret(
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_BOT_ID",
            "TELEGRAM_TOKEN",
            "BOT_TOKEN",
            "BOT_ID",
            "TG_BOT_TOKEN",
            "TG_TOKEN",
        )

    @property
    def chat_id(self) -> str | None:
        """Main chat ID for core high-value alerts (>= ₹1.00)."""
        return self._chat_id or _get_secret(
            "TELEGRAM_CHAT_ID",
            "TELEGRAM_CHATID",
            "TELEGRAM_GROUP_ID",
            "CHAT_ID",
            "CHATID",
            "GROUP_ID",
            "TG_CHAT_ID",
        )

    @property
    def penny_bot_token(self) -> str | None:
        """Dedicated bot token for penny decay alerts (< ₹1.00), falls back to main bot token."""
        return self._penny_bot_token or _get_secret(
            "TELEGRAM_PENNY_BOT_TOKEN",
            "TELEGRAM_BOT_TOKEN_PENNY",
            "PENNY_BOT_TOKEN",
            "PENNY_BOT_ID",
        ) or self.bot_token

    @property
    def penny_chat_id(self) -> str | None:
        """Dedicated chat ID for penny decay alerts (< ₹1.00)."""
        return self._penny_chat_id or _get_secret(
            "TELEGRAM_PENNY_CHAT_ID",
            "TELEGRAM_CHAT_ID_PENNY",
            "PENNY_CHAT_ID",
            "PENNY_GROUP_ID",
            "TELEGRAM_PENNY_GROUP_ID",
        )

    def is_configured(self) -> bool:
        """Return whether Primary Core Telegram credentials are available."""
        return bool(self.bot_token and self.chat_id)

    def is_penny_configured(self) -> bool:
        """Return whether Penny Decay Telegram credentials are available."""
        return bool(self.penny_bot_token and self.penny_chat_id)

    def _dispatch_loop(self) -> None:
        """Background worker that drains queue and sends alerts respecting rate limits."""
        while True:
            try:
                item = self._msg_queue.get()
                if item is None:
                    break
                event, is_penny = item
                self._send_immediate(event, is_penny=is_penny)
                time.sleep(1.2)  # Max 1 message per 1.2s to strictly respect Telegram rate limits
            except Exception as e:
                logger.error("Error in Telegram dispatch worker: %s", e)
                time.sleep(2.0)

    def format_message(self, event: ExtremeEvent) -> str:
        """Create a human-readable Telegram message for stock or option alerts with tiered severity."""
        duration_minutes = event.duration_seconds / 60

        # Tiered severity headers based on threshold percentage
        if event.direction.value == "UP":
            severity_badge = f"🚀 [SPIKE UP +{event.threshold:.0f}%] 🟢"
            status_icon = "🟢"
        elif event.threshold >= 80.0:
            severity_badge = "🚨 [CRITICAL CRASH -80%] 🔴"
            status_icon = "🔴"
        elif event.threshold >= 70.0:
            severity_badge = "⚠️ [HIGH ALERT -70%] 🟡"
            status_icon = "🟡"
        else:
            severity_badge = "📉 [TRIGGER -60%] 🟢"
            status_icon = "🟢"

        if event.is_penny_decay:
            title = f"{severity_badge}\n📉 EXTREME PENNY / EXPIRY DECAY (< ₹1.00)"
            contract_info = (
                f"📊 Contract: {event.symbol} {event.strike_price:.0f} {event.option_type.value}\n"
                f"🏷️ Type: {'CALL (CE)' if event.option_type.value == 'CE' else 'PUT (PE)'}\n"
                f"🎯 Strike: ₹{event.strike_price:.0f} | Expiry: {event.expiry}\n"
                f"💰 Start Premium: ₹{event.start_price:.2f} ➔ Current: ₹{event.current_price:.2f}\n"
            )
            if event.underlying_price > 0:
                contract_info += f"📈 Underlying Spot: ₹{event.underlying_price:.2f}\n"
        elif event.is_option and event.option_type is not None:
            title = f"{severity_badge}\n🚨 EXTREME OPTION MOVEMENT DETECTED"
            contract_info = (
                f"📊 Contract: {event.symbol} {event.strike_price:.0f} {event.option_type.value}\n"
                f"🏷️ Type: {'CALL (CE)' if event.option_type.value == 'CE' else 'PUT (PE)'}\n"
                f"🎯 Strike: ₹{event.strike_price:.0f} | Expiry: {event.expiry}\n"
                f"💰 Start Premium: ₹{event.start_price:.2f} ➔ Current: ₹{event.current_price:.2f}\n"
            )
            if event.underlying_price > 0:
                contract_info += f"📈 Underlying Spot: ₹{event.underlying_price:.2f}\n"
        else:
            title = f"{severity_badge}\n🚨 EXTREME MOVEMENT DETECTED"
            contract_info = (
                f"📊 Stock: {event.symbol}\n"
                f"💰 Start Price: ₹{event.start_price:.2f} ➔ Current: ₹{event.current_price:.2f}\n"
            )

        return (
            f"{title}\n\n"
            f"{status_icon} Stock: {event.symbol}\n"
            f"Movement: {event.percentage_change:+.2f}%\n"
            f"Direction: {event.direction.value}\n"
            f"Threshold: {event.threshold:.0f}%\n"
            f"{contract_info}"
            f"Duration: {duration_minutes:.1f} minutes\n"
            f"Time: {event.current_timestamp:%H:%M:%S}"
        )

    def _send_immediate(self, event: ExtremeEvent, is_penny: bool = False) -> bool:
        """Perform actual HTTP POST to the appropriate Telegram bot and channel."""
        if is_penny:
            if not self.is_penny_configured():
                return False
            token = self.penny_bot_token
            chat_id = self.penny_chat_id
        else:
            if not self.is_configured():
                return False
            token = self.bot_token
            chat_id = self.chat_id

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            response = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": self.format_message(event),
                },
                timeout=10,
            )
            if response.status_code == 429:
                retry_after = 5
                try:
                    retry_after = int(response.json().get("parameters", {}).get("retry_after", 5))
                except Exception:
                    pass
                logger.warning("Telegram 429 rate limit hit. Pausing for %ds", retry_after)
                time.sleep(retry_after)
                return False

            response.raise_for_status()
            return bool(response.json().get("ok"))
        except Exception as e:
            logger.error("Failed to post message to Telegram (%s): %s", "Penny" if is_penny else "Core", e)
            return False

    def send(self, event: ExtremeEvent) -> bool:
        """Enqueue event for rate-limited async dispatch to the appropriate bot/channel."""
        is_penny = event.is_penny_decay
        if is_penny:
            if not self.is_penny_configured():
                # Penny channel is not configured yet; don't clutter the main bot
                return False
        else:
            if not self.is_configured():
                return False

        try:
            self._msg_queue.put_nowait((event, is_penny))
            return True
        except queue.Full:
            logger.warning("Telegram queue full, dropping alert")
            return False

    def send_test_message(self) -> bool:
        """Send a test ping message to verify Primary Core Telegram bot setup."""
        if not self.is_configured():
            raise RuntimeError("Primary Telegram credentials missing in Secrets or .env (TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID)")

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        response = requests.post(
            url,
            json={
                "chat_id": self.chat_id,
                "text": (
                    "⚡ <b>F&O Extreme Movement Monitor — Core Bot</b>\n\n"
                    "✅ <b>Primary Core Bot Connected Successfully!</b>\n"
                    "This channel will receive high-conviction alerts for <b>₹1.00+ option crashes</b> (-60%, -70%, -80%)."
                ),
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        response.raise_for_status()
        return bool(response.json().get("ok"))

    def send_test_penny_message(self) -> bool:
        """Send a test ping message to verify Expiry Penny Decay Telegram bot setup."""
        if not self.is_penny_configured():
            raise RuntimeError("Penny Telegram credentials missing in Secrets or .env (TELEGRAM_PENNY_CHAT_ID)")

        url = f"https://api.telegram.org/bot{self.penny_bot_token}/sendMessage"
        response = requests.post(
            url,
            json={
                "chat_id": self.penny_chat_id,
                "text": (
                    "📉 <b>F&O Option Monitor — Expiry Penny Bot</b>\n\n"
                    "✅ <b>Penny & Expiry Decay Bot Connected Successfully!</b>\n"
                    "This dedicated channel will receive alerts for <b>sub-₹1.00 penny decay</b>."
                ),
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        response.raise_for_status()
        return bool(response.json().get("ok"))