import logging
import os
import threading
from datetime import datetime
from typing import Optional

from app.alerts.manager import AlertManager
from app.data.dhan import DhanMarketDataProvider
from app.data.dummy import DummyMarketDataProvider
from app.data.models import MarketTick, OptionTick
from app.detection.detector import ExtremeDetector, ExtremeEvent
from app.movement.calculator import calculate_movement
from app.notifications.telegram import TelegramNotifier
from app.state.manager import StateManager

logger = logging.getLogger(__name__)


class MonitorEngine:
    """
    Server-wide shared singleton engine that manages:
    - Global StateManager for 210 F&O stocks
    - Background DhanHQ WebSocket live tick pipeline
    - Extreme movement detector (-60%, -70%, -80% down crashes)
    - Deduplicated Telegram notifications (dispatched exactly once)
    - Synchronized live alert state across all connected devices (laptop/mobile)
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state_manager = StateManager(window_minutes=60)
        self.detector = ExtremeDetector(thresholds=(60.0, 70.0, 80.0), allow_up=False, allow_down=True)
        self.alert_manager = AlertManager()
        self.notifier = TelegramNotifier()
        self.dhan_provider = DhanMarketDataProvider(strikes_above_below=6)
        self.dummy_provider = DummyMarketDataProvider(
            update_interval_seconds=0,
            strikes_above_below=4,
            enable_random_spikes=True,
        )
        self.recent_alerts: list[ExtremeEvent] = []
        self.live_ticks_received: int = 0
        self.update_count: int = 0
        self.is_streaming_active: bool = False
        self.cached_real_chains: dict[str, dict] = {}

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

            for event in events:
                if self.alert_manager.process(event):
                    new_alerts.append(event)
                    self.recent_alerts.insert(0, event)
                    if len(self.recent_alerts) > 100:
                        self.recent_alerts = self.recent_alerts[:100]

                    # Dispatch to Telegram exactly once globally
                    if self.notifier.is_configured():
                        try:
                            self.notifier.send(event)
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
