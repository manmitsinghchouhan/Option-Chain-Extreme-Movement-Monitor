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


@dataclass
class PinnedTrade:
    """Represents a trade bookmarked by the user from an extreme movement alert."""

    symbol: str
    strike_price: float
    option_type: OptionType | None
    expiry: str
    pinned_price: float  # Price at the moment of pinning (entry)
    pinned_timestamp: datetime
    instrument_key: str
    threshold: float = 0.0
    percentage_change: float = 0.0  # Alert percentage change at time of pinning
    underlying_price: float = 0.0
    start_price: float = 0.0  # Alert start price before the extreme drop

    @property
    def display_title(self) -> str:
        """Human-readable contract description."""
        if self.option_type is not None and self.strike_price > 0:
            return f"{self.symbol} {self.strike_price:.0f} {self.option_type.value}"
        return self.symbol

    def to_dict(self) -> dict:
        """Serialize pinned trade to JSON-compatible dictionary."""
        return {
            "symbol": self.symbol,
            "strike_price": self.strike_price,
            "option_type": self.option_type.value if self.option_type else None,
            "expiry": self.expiry,
            "pinned_price": self.pinned_price,
            "pinned_timestamp": self.pinned_timestamp.isoformat(),
            "instrument_key": self.instrument_key,
            "threshold": self.threshold,
            "percentage_change": self.percentage_change,
            "underlying_price": self.underlying_price,
            "start_price": self.start_price,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PinnedTrade":
        """Deserialize pinned trade from dictionary."""
        opt_type_val = data.get("option_type")
        opt_type = OptionType(opt_type_val) if opt_type_val else None
        return cls(
            symbol=data["symbol"],
            strike_price=float(data.get("strike_price", 0.0)),
            option_type=opt_type,
            expiry=data.get("expiry", ""),
            pinned_price=float(data["pinned_price"]),
            pinned_timestamp=datetime.fromisoformat(data["pinned_timestamp"]),
            instrument_key=data.get("instrument_key", ""),
            threshold=float(data.get("threshold", 0.0)),
            percentage_change=float(data.get("percentage_change", 0.0)),
            underlying_price=float(data.get("underlying_price", 0.0)),
            start_price=float(data.get("start_price", 0.0)),
        )
