from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.data.models import OptionType
from app.movement.calculator import MovementResult


class MovementDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


@dataclass(frozen=True)
class ExtremeEvent:
    """Represents an extreme stock or option premium movement."""

    symbol: str
    direction: MovementDirection
    threshold: float
    percentage_change: float
    start_price: float
    current_price: float
    start_timestamp: datetime
    current_timestamp: datetime
    duration_seconds: float
    strike_price: float = 0.0
    option_type: OptionType | None = None
    expiry: str = ""
    instrument_key: str = ""
    underlying_price: float = 0.0

    @property
    def is_option(self) -> bool:
        """True if event is on an Option contract."""
        return self.option_type is not None and self.strike_price > 0

    @property
    def display_title(self) -> str:
        """Human-readable description for UI & Telegram."""
        if self.is_option and self.option_type is not None:
            return f"{self.symbol} {self.strike_price:.0f} {self.option_type.value}"
        return self.symbol


class ExtremeDetector:
    """Detects movements that cross configured extreme thresholds."""

    DEFAULT_THRESHOLDS = (60.0, 70.0, 80.0)

    def __init__(
        self,
        thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
    ) -> None:
        if not thresholds:
            raise ValueError("At least one threshold is required.")

        if any(threshold <= 0 for threshold in thresholds):
            raise ValueError("Thresholds must be greater than zero.")

        self.thresholds = tuple(sorted(set(thresholds)))

    def detect(
        self,
        symbol: str,
        movement: MovementResult,
        strike_price: float = 0.0,
        option_type: OptionType | None = None,
        expiry: str = "",
        instrument_key: str = "",
        underlying_price: float = 0.0,
    ) -> list[ExtremeEvent]:
        """
        Detect every configured threshold crossed by the movement.

        Example:
            +80% movement -> +60%, +70%, +80%
        """

        percentage = movement.percentage_change

        if percentage == 0:
            return []

        if percentage > 0:
            direction = MovementDirection.UP
            crossed_thresholds = [
                threshold
                for threshold in self.thresholds
                if percentage >= threshold
            ]
        else:
            direction = MovementDirection.DOWN
            absolute_percentage = abs(percentage)
            crossed_thresholds = [
                threshold
                for threshold in self.thresholds
                if absolute_percentage >= threshold
            ]

        key = instrument_key or (
            f"{symbol}_{expiry}_{strike_price:.0f}_{option_type.value}"
            if option_type is not None
            else symbol
        )

        return [
            ExtremeEvent(
                symbol=symbol,
                direction=direction,
                threshold=threshold,
                percentage_change=percentage,
                start_price=movement.start_price,
                current_price=movement.current_price,
                start_timestamp=movement.start_timestamp,
                current_timestamp=movement.current_timestamp,
                duration_seconds=movement.duration_seconds,
                strike_price=strike_price,
                option_type=option_type,
                expiry=expiry,
                instrument_key=key,
                underlying_price=underlying_price,
            )
            for threshold in crossed_thresholds
        ]