import logging
import os
import queue
import threading
import time

from datetime import datetime, timezone, timedelta
import requests

from app.detection.detector import ExtremeEvent

IST = timezone(timedelta(hours=5, minutes=30))

def format_ist_time(dt: datetime) -> str:
    """Format any datetime (UTC or naive) into IST HH:MM:SS string."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%H:%M:%S")

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
    """Sends extreme movement notifications through Telegram with 3-tier routing (Core Stocks, Indices, Penny Decay) and background rate-limiting."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        index_bot_token: str | None = None,
        index_chat_id: str | None = None,
        penny_bot_token: str | None = None,
        penny_chat_id: str | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._index_bot_token = index_bot_token
        self._index_chat_id = index_chat_id
        self._penny_bot_token = penny_bot_token
        self._penny_chat_id = penny_chat_id

        self._core_queue: queue.Queue = queue.Queue(maxsize=1000)
        self._index_queue: queue.Queue = queue.Queue(maxsize=1000)
        self._penny_queue: queue.Queue = queue.Queue(maxsize=1000)

        self._core_worker = threading.Thread(target=self._core_dispatch_loop, daemon=True)
        self._core_worker.start()

        self._index_worker = threading.Thread(target=self._index_dispatch_loop, daemon=True)
        self._index_worker.start()

        self._penny_worker = threading.Thread(target=self._penny_dispatch_loop, daemon=True)
        self._penny_worker.start()

    @property
    def bot_token(self) -> str | None:
        """Main bot token for core stock alerts (>= ₹1.00)."""
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
        """Main chat ID for core stock alerts (>= ₹1.00)."""
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
    def index_bot_token(self) -> str | None:
        """Dedicated bot token for Index options (NIFTY/SENSEX >= ₹1.00), falls back to main bot token."""
        return self._index_bot_token or _get_secret(
            "TELEGRAM_INDEX_BOT_TOKEN",
            "TELEGRAM_BOT_TOKEN_INDEX",
            "INDEX_BOT_TOKEN",
            "INDEX_BOT_ID",
        ) or self.bot_token

    @property
    def index_chat_id(self) -> str | None:
        """Dedicated chat ID for Index options (NIFTY/SENSEX >= ₹1.00)."""
        return self._index_chat_id or _get_secret(
            "TELEGRAM_INDEX_CHAT_ID",
            "TELEGRAM_CHAT_ID_INDEX",
            "INDEX_CHAT_ID",
            "INDEX_GROUP_ID",
            "TELEGRAM_INDEX_GROUP_ID",
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
        """Return whether Primary Core Stock Telegram credentials are available."""
        return bool(self.bot_token and self.chat_id)

    def is_index_configured(self) -> bool:
        """Return whether Dedicated Index Option Telegram credentials are available."""
        return bool(self.index_bot_token and self.index_chat_id)

    def is_penny_configured(self) -> bool:
        """Return whether Penny Decay Telegram credentials are available."""
        return bool(self.penny_bot_token and self.penny_chat_id)

    def _core_dispatch_loop(self) -> None:
        """Dedicated background worker for Core Stock channel (>= ₹1.00)."""
        while True:
            try:
                event = self._core_queue.get()
                if event is None:
                    break
                self._send_immediate(event, channel_type="core")
                time.sleep(0.8)  # Smooth rate limiting
            except Exception as e:
                logger.error("Error in Core Telegram dispatch worker: %s", e)
                time.sleep(1.5)

    def _index_dispatch_loop(self) -> None:
        """Dedicated background worker for Index Options channel (NIFTY/SENSEX >= ₹1.00)."""
        while True:
            try:
                event = self._index_queue.get()
                if event is None:
                    break
                self._send_immediate(event, channel_type="index")
                time.sleep(0.8)  # Smooth rate limiting
            except Exception as e:
                logger.error("Error in Index Telegram dispatch worker: %s", e)
                time.sleep(1.5)

    def _penny_dispatch_loop(self) -> None:
        """Dedicated background worker for Expiry Penny channel (< ₹1.00)."""
        while True:
            try:
                event = self._penny_queue.get()
                if event is None:
                    break
                self._send_immediate(event, channel_type="penny")
                time.sleep(0.8)  # Smooth rate limiting
            except Exception as e:
                logger.error("Error in Penny Telegram dispatch worker: %s", e)
                time.sleep(1.5)

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
            is_idx = str(event.symbol).upper() in ("NIFTY", "SENSEX")
            title = f"{severity_badge}\n{'🎯' if is_idx else '🚨'} EXTREME {'INDEX' if is_idx else 'OPTION'} MOVEMENT DETECTED"
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
            f"{status_icon} Instrument: {event.symbol}\n"
            f"Movement: {event.percentage_change:+.2f}%\n"
            f"Direction: {event.direction.value}\n"
            f"Threshold: {event.threshold:.0f}%\n"
            f"{contract_info}"
            f"Duration: {duration_minutes:.1f} minutes\n"
            f"Time: {format_ist_time(event.current_timestamp)} IST"
        )

    def _send_immediate(self, event: ExtremeEvent, channel_type: str = "core") -> bool:
        """Perform actual HTTP POST to the appropriate Telegram bot and channel."""
        if channel_type == "penny":
            if not self.is_penny_configured():
                return False
            token = self.penny_bot_token
            chat_id = self.penny_chat_id
        elif channel_type == "index":
            if not self.is_index_configured():
                return False
            token = self.index_bot_token
            chat_id = self.index_chat_id
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
                logger.warning("Telegram 429 rate limit hit (%s). Pausing for %ds", channel_type, retry_after)
                time.sleep(retry_after)
                return False

            response.raise_for_status()
            return bool(response.json().get("ok"))
        except Exception as e:
            logger.error("Failed to post message to Telegram (%s): %s", channel_type, e)
            return False

    def send(self, event: ExtremeEvent) -> bool:
        """Enqueue event for rate-limited async dispatch to the appropriate bot/channel."""
        is_penny = event.is_penny_decay
        is_index = str(event.symbol).upper() in ("NIFTY", "SENSEX")

        if is_penny:
            if not self.is_penny_configured():
                return False
            target_queue = self._penny_queue
        elif is_index and self.is_index_configured():
            target_queue = self._index_queue
        else:
            if not self.is_configured():
                return False
            target_queue = self._core_queue

        try:
            target_queue.put_nowait(event)
            return True
        except queue.Full:
            # If queue is full during extreme volatility burst, drop the oldest stale message and keep latest
            try:
                target_queue.get_nowait()
                target_queue.put_nowait(event)
                return True
            except Exception:
                return False

    def send_test_message(self) -> bool:
        """Send a test ping message to verify Primary Core Stock Telegram bot setup."""
        if not self.is_configured():
            raise RuntimeError("Primary Stock Telegram credentials missing in Secrets or .env (TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID)")

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        response = requests.post(
            url,
            json={
                "chat_id": self.chat_id,
                "text": (
                    "⚡ <b>F&O Extreme Movement Monitor — Core Stocks Bot</b>\n\n"
                    "✅ <b>Primary Stock Bot Connected Successfully!</b>\n"
                    "This channel will receive high-conviction alerts for <b>₹1.00+ stock option crashes</b> (-60%, -70%, -80%)."
                ),
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        response.raise_for_status()
        return bool(response.json().get("ok"))

    def send_test_index_message(self) -> bool:
        """Send a test ping message to verify Dedicated Index Options Telegram bot setup."""
        if not self.is_index_configured():
            raise RuntimeError("Index Telegram credentials missing in Secrets or .env (TELEGRAM_INDEX_CHAT_ID)")

        url = f"https://api.telegram.org/bot{self.index_bot_token}/sendMessage"
        response = requests.post(
            url,
            json={
                "chat_id": self.index_chat_id,
                "text": (
                    "🎯 <b>F&O Extreme Movement Monitor — Dedicated Index Bot</b>\n\n"
                    "✅ <b>NIFTY 50 & SENSEX Index Channel Connected Successfully!</b>\n"
                    "This channel will receive alerts exclusively for <b>NIFTY & SENSEX index option crashes</b>."
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