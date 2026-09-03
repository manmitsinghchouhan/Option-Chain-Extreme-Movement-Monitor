from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.data.models import MarketTick, OptionTick


@dataclass(frozen=True)
class PriceObservation:
    """A price/premium observation stored by the state manager."""

    timestamp: datetime
    price: float
    volume: int
    open_interest: int = 0
    underlying_price: float = 0.0


class StateManager:
    """Maintains recent price and option premium history for all monitored instruments."""

    def __init__(self, window_minutes: int = 60) -> None:
        if window_minutes <= 0:
            raise ValueError("window_minutes must be greater than zero.")

        self.window = timedelta(minutes=window_minutes)
        self._history: dict[str, deque[PriceObservation]] = {}
        # Mapping from symbol -> set of instrument keys (e.g. "TCS" -> {"TCS_..._CE", "TCS_..._PE"})
        self._symbol_instruments: dict[str, set[str]] = {}
        # Latest underlying spot prices
        self._underlying_prices: dict[str, float] = {}

    def update(self, tick: OptionTick | MarketTick) -> None:
        """Add a new tick to the appropriate instrument history."""
        if isinstance(tick, OptionTick):
            key = tick.instrument_key
            price = tick.premium
            vol = tick.volume
            oi = tick.open_interest
            underlying = tick.underlying_price

            if tick.symbol not in self._symbol_instruments:
                self._symbol_instruments[tick.symbol] = set()
            self._symbol_instruments[tick.symbol].add(key)
            if underlying > 0:
                self._underlying_prices[tick.symbol] = underlying
        else:
            key = tick.symbol
            price = tick.price
            vol = tick.volume
            oi = 0
            underlying = tick.price
            self._underlying_prices[tick.symbol] = price

        if price <= 0:
            raise ValueError("Price must be greater than zero.")

        observation = PriceObservation(
            timestamp=tick.timestamp,
            price=price,
            volume=vol,
            open_interest=oi,
            underlying_price=underlying,
        )

        if key not in self._history:
            self._history[key] = deque()

        history = self._history[key]
        history.append(observation)
        self._remove_old_observations(key)

    def _remove_old_observations(self, key: str) -> None:
        """Remove observations that are outside the rolling time window."""
        history = self._history[key]
        if not history:
            return

        newest_timestamp = history[-1].timestamp
        cutoff = newest_timestamp - self.window

        while history and history[0].timestamp < cutoff:
            history.popleft()

    def get_history(self, key: str) -> list[PriceObservation]:
        """Return the current rolling history for an instrument."""
        return list(self._history.get(key, []))

    def get_latest(self, key: str) -> PriceObservation | None:
        """Return the latest observation for an instrument."""
        history = self._history.get(key)
        if not history:
            return None
        return history[-1]

    def get_underlying_price(self, symbol: str) -> float | None:
        """Return the latest underlying spot price for a stock."""
        return self._underlying_prices.get(symbol)

    def get_instruments_for_symbol(self, symbol: str) -> list[str]:
        """Return all tracked option instrument keys for a symbol."""
        return sorted(list(self._symbol_instruments.get(symbol, set())))

    def get_observation_count(self, key: str) -> int:
        """Return the number of stored observations for an instrument/stock."""
        return len(self._history.get(key, []))

    def get_instrument_count(self) -> int:
        """Return the total number of option contracts currently tracked."""
        return len(self._history)


    def get_stock_count(self) -> int:
        """Return the number of underlying stocks tracked."""
        return len(self._symbol_instruments) or len(self._history)

    def clear(self) -> None:
        """Remove all stored market history."""
        self._history.clear()
        self._symbol_instruments.clear()
        self._underlying_prices.clear()