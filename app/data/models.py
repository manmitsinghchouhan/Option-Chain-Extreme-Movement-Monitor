from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class OptionType(str, Enum):
    CE = "CE"  # Call Option
    PE = "PE"  # Put Option


@dataclass(frozen=True)
class MarketTick:
    """Represents one market-price update for an underlying stock."""

    symbol: str
    price: float
    timestamp: datetime
    volume: int


@dataclass(frozen=True)
class OptionTick:
    """Represents one market-price update for an option contract."""

    symbol: str
    strike_price: float
    option_type: OptionType
    expiry: str
    premium: float
    timestamp: datetime
    volume: int
    open_interest: int = 0
    underlying_price: float = 0.0

    @property
    def price(self) -> float:
        """Alias for option premium."""
        return self.premium

    @property
    def instrument_key(self) -> str:
        """Unique identifier for the option contract."""
        return f"{self.symbol}_{self.expiry}_{self.strike_price:.0f}_{self.option_type.value}"

    @property
    def display_name(self) -> str:
        """Human-readable display name, e.g. TCS 4000 CE."""
        return f"{self.symbol} {self.strike_price:.0f} {self.option_type.value}"
