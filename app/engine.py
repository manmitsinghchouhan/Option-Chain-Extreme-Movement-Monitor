import json
import logging
import os
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))

from app.alerts.manager import AlertManager
from app.data.dhan import DhanMarketDataProvider
from app.data.dummy import DummyMarketDataProvider
from app.data.models import MarketTick, OptionTick, PinnedTrade
from app.detection.detector import ExtremeDetector, ExtremeEvent
from app.movement.calculator import calculate_movement
from app.notifications.telegram import TelegramNotifier
from app.state.manager import StateManager

logger = logging.getLogger(__name__)

PINNED_FILE_PATH = Path(__file__).resolve().parent.parent / "pinned_trades.json"


class MonitorEngine:
    """
    Server-wide shared singleton engine that manages:
    - Global StateManager for 210 F&O stocks
    - Background DhanHQ WebSocket live tick pipeline
    - Extreme movement detector (-60%, -70%, -80% down crashes)
    - Deduplicated Telegram notifications (dispatched exactly once)
    - Synchronized live alert state & pinned active trades across all connected devices
    """

    _instance: Optional["MonitorEngine"] = None
    _singleton_lock: threading.RLock = threading.RLock()

    @classmethod
    def get_instance(cls) -> "MonitorEngine":
        """Get or create the global singleton engine instance safely without inspect.getsource."""
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.state_manager = StateManager(window_minutes=60)
        self.detector = ExtremeDetector(thresholds=(60.0, 70.0, 80.0), allow_up=False, allow_down=True)
        self.alert_manager = AlertManager()
        self.notifier = TelegramNotifier()
        self.dhan_provider = DhanMarketDataProvider(
            strikes_above_below=5,
            index_strikes_above_below=10,
        )
        self.dummy_provider = DummyMarketDataProvider(
            update_interval_seconds=0,
            strikes_above_below=5,
            index_strikes_above_below=10,
            enable_random_spikes=True,
        )
        self.recent_alerts: list[ExtremeEvent] = []
        self.pinned_trades: dict[str, PinnedTrade] = {}
        self.live_ticks_received: int = 0
        self.update_count: int = 0
        self.is_streaming_active: bool = False
        self.cached_real_chains: dict[str, dict] = {}
        self._load_pinned_trades()

    @property
    def stock_alerts(self) -> list[ExtremeEvent]:
        """High-conviction stock option crashes (>= ₹1.00, non-index)."""
        with self.lock:
            return [e for e in self.recent_alerts if not e.is_penny_decay and str(e.symbol).upper() not in ("NIFTY", "SENSEX")]

    @property
    def index_alerts(self) -> list[ExtremeEvent]:
        """High-conviction index option crashes (NIFTY & SENSEX >= ₹1.00)."""
        with self.lock:
            return [e for e in self.recent_alerts if not e.is_penny_decay and str(e.symbol).upper() in ("NIFTY", "SENSEX")]

    @property
    def core_alerts(self) -> list[ExtremeEvent]:
        """Backward-compatibility alias for stock_alerts."""
        return self.stock_alerts

    @property
    def penny_alerts(self) -> list[ExtremeEvent]:
        """Expiry-week sub-₹1.00 penny decay alerts."""
        with self.lock:
            return [e for e in self.recent_alerts if e.is_penny_decay]

    def process_tick(self, tick: MarketTick | OptionTick) -> list[ExtremeEvent]:
        """Process incoming tick, update state, detect extreme crashes, and dispatch alerts."""
        new_alerts: list[ExtremeEvent] = []

        with self.lock:
            self.state_manager.update(tick)
            self.live_ticks_received += 1

            if not isinstance(tick, OptionTick):
                return new_alerts

            history = self.state_manager.get_history(tick.instrument_key)
            if len(history) < 2:
                return new_alerts

            movement = calculate_movement(history)
            if movement is None:
                return new_alerts

            self.alert_manager.sync_resets(
                instrument_key=tick.instrument_key,
                percentage_change=movement.percentage_change,
                thresholds=self.detector.thresholds,
            )

            events = self.detector.detect(
                symbol=tick.symbol,
                movement=movement,
                strike_price=tick.strike_price,
                option_type=tick.option_type,
                expiry=tick.expiry,
                instrument_key=tick.instrument_key,
                underlying_price=tick.underlying_price,
            )

            tick_new_alerts = []
            for event in events:
                if self.alert_manager.process(event):
                    tick_new_alerts.append(event)
                    new_alerts.append(event)
                    self.recent_alerts.insert(0, event)

            highest_event = None
            if tick_new_alerts:
                if len(self.recent_alerts) > 100:
                    self.recent_alerts = self.recent_alerts[:100]
                highest_event = max(tick_new_alerts, key=lambda e: e.threshold)

        # Dispatch outside the engine lock to prevent blocking live WebSocket & UI threads
        if highest_event is not None:
            try:
                self.notifier.send(highest_event)
            except Exception as e:
                logger.error("Failed to send Telegram alert: %s", e)

        return new_alerts

    def start_dhan_feed(self) -> None:
        """Start background DhanHQ WebSocket streaming."""
        with self.lock:
            self.is_streaming_active = True
            self.dhan_provider.last_error = None
            if not self.dhan_provider._running:
                self.dhan_provider.start_background_feed(on_tick_callback=self.process_tick)

    def stop_dhan_feed(self) -> None:
        """Stop background DhanHQ WebSocket streaming."""
        with self.lock:
            self.is_streaming_active = False
            if self.dhan_provider._running:
                self.dhan_provider.stop()

    def update_dhan_credentials(self, client_id: str, access_token: str) -> None:
        """Update credentials dynamically and reconnect if active."""
        with self.lock:
            if client_id:
                os.environ["DHAN_CLIENT_ID"] = client_id
                self.dhan_provider.client_id = client_id
            if access_token:
                os.environ["DHAN_ACCESS_TOKEN"] = access_token
                self.dhan_provider.access_token = access_token

            self.dhan_provider.last_error = None
            if self.dhan_provider._running:
                self.dhan_provider.stop()
            
            # If streaming is enabled, immediately launch the new feed with fresh token!
            if self.is_streaming_active:
                self.dhan_provider.start_background_feed(on_tick_callback=self.process_tick)

            self.cached_real_chains.clear()

    def step_simulation(self) -> list[ExtremeEvent]:
        """Generate one batch of simulation ticks and process them."""
        ticks = self.dummy_provider.generate_option_ticks()
        all_new: list[ExtremeEvent] = []
        for t in ticks:
            all_new.extend(self.process_tick(t))
        with self.lock:
            self.update_count += 1
        return all_new

    def inject_simulation_spike(self, percentage_change: float = -75.0) -> str:
        """Inject a simulated extreme price drop."""
        spiked_key = self.dummy_provider.inject_extreme_spike(percentage_change=percentage_change)
        self.step_simulation()
        return spiked_key

    def clear_state(self) -> None:
        """Reset state, alert history, and tick counters."""
        with self.lock:
            self.state_manager.clear()
            self.alert_manager.clear()
            self.recent_alerts.clear()
            self.live_ticks_received = 0
            self.update_count = 0

    @property
    def pinned_trades_list(self) -> list[PinnedTrade]:
        """Return all active pinned trades sorted newest first."""
        with self.lock:
            if not hasattr(self, "pinned_trades"):
                self.pinned_trades = {}
                self._load_pinned_trades()
            return sorted(self.pinned_trades.values(), key=lambda p: p.pinned_timestamp, reverse=True)

    def pin_trade(self, event: ExtremeEvent) -> PinnedTrade:
        """Pin a trade from an extreme alert event and save to disk."""
        with self.lock:
            if not hasattr(self, "pinned_trades"):
                self.pinned_trades = {}
            key = event.instrument_key or event.symbol
            entry_price = event.current_price if event.current_price > 0 else event.start_price
            pinned = PinnedTrade(
                symbol=event.symbol,
                strike_price=event.strike_price,
                option_type=event.option_type,
                expiry=event.expiry,
                pinned_price=entry_price,
                pinned_timestamp=datetime.now(IST),
                instrument_key=key,
                threshold=event.threshold,
                percentage_change=event.percentage_change,
                underlying_price=event.underlying_price,
            )
            self.pinned_trades[key] = pinned
            self._save_pinned_trades()
            return pinned

    def unpin_trade(self, instrument_key: str) -> bool:
        """Unpin a trade and save to disk."""
        with self.lock:
            if not hasattr(self, "pinned_trades"):
                self.pinned_trades = {}
            if instrument_key in self.pinned_trades:
                del self.pinned_trades[instrument_key]
                self._save_pinned_trades()
                return True
            return False

    def clear_pinned_trades(self) -> None:
        """Clear all active pinned trades and save to disk."""
        with self.lock:
            if not hasattr(self, "pinned_trades"):
                self.pinned_trades = {}
            self.pinned_trades.clear()
            self._save_pinned_trades()

    def _save_pinned_trades(self) -> None:
        """Persist pinned trades to JSON file."""
        try:
            data = [p.to_dict() for p in self.pinned_trades.values()]
            with open(PINNED_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error("Failed to save pinned trades to %s: %s", PINNED_FILE_PATH, e)

    def _load_pinned_trades(self) -> None:
        """Load pinned trades from JSON file on server startup."""
        if not PINNED_FILE_PATH.exists():
            return
        try:
            with open(PINNED_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        trade = PinnedTrade.from_dict(item)
                        self.pinned_trades[trade.instrument_key] = trade
            logger.info("Loaded %d pinned trades from disk", len(self.pinned_trades))
        except Exception as e:
            logger.error("Failed to load pinned trades from %s: %s", PINNED_FILE_PATH, e)
