from dataclasses import dataclass
from datetime import datetime

from app.state.manager import PriceObservation


@dataclass(frozen=True)
class MovementResult:
    """Result of a price or option premium movement calculation."""

    start_price: float
    current_price: float
    percentage_change: float
    start_timestamp: datetime
    current_timestamp: datetime
    duration_seconds: float
    min_price: float = 0.0
    max_price: float = 0.0


def calculate_movement(
    observations: list[PriceObservation],
) -> MovementResult | None:
    """
    Calculate movement from the earliest available observation
    to the latest observation in the rolling window.
    """

    if len(observations) < 2:
        return None

    start = observations[0]
    current = observations[-1]

    if start.price <= 0:
        raise ValueError("Starting price must be greater than zero.")

    percentage_change = ((current.price - start.price) / start.price) * 100
    duration_seconds = (current.timestamp - start.timestamp).total_seconds()

    all_prices = [obs.price for obs in observations]
    min_price = min(all_prices)
    max_price = max(all_prices)

    return MovementResult(
        start_price=start.price,
        current_price=current.price,
        percentage_change=percentage_change,
        start_timestamp=start.timestamp,
        current_timestamp=current.timestamp,
        duration_seconds=duration_seconds,
        min_price=min_price,
        max_price=max_price,
    )