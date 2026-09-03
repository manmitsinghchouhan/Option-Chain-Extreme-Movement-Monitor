from dataclasses import dataclass

from app.detection.detector import ExtremeEvent


@dataclass(frozen=True)
class AlertKey:
    """Uniquely identifies one threshold crossing event for an instrument."""

    instrument_key: str
    direction: str
    threshold: float


class AlertManager:
    """
    Prevents repeated alerts while an extreme movement remains active.

    A threshold can trigger again only after the movement falls back
    below that threshold and crosses it again.
    """

    def __init__(self) -> None:
        self._active: set[AlertKey] = set()

    def process(self, event: ExtremeEvent) -> bool:
        """
        Process an extreme event.

        Returns:
            True  -> this is a new alert.
            False -> this event is already active.
        """

        key_identifier = event.instrument_key or event.symbol

        key = AlertKey(
            instrument_key=key_identifier,
            direction=event.direction.value,
            threshold=event.threshold,
        )

        if key in self._active:
            return False

        self._active.add(key)
        return True

    def reset_below_threshold(
        self,
        instrument_key: str | None = None,
        direction: str = "DOWN",
        threshold: float = 60.0,
        symbol: str | None = None,
    ) -> None:
        """Allow the threshold to trigger again."""

        key_identifier = instrument_key or symbol or ""

        key = AlertKey(
            instrument_key=key_identifier,
            direction=direction,
            threshold=threshold,
        )

        self._active.discard(key)


    def clear(self) -> None:
        """Clear all active alert states."""

        self._active.clear()

    def active_count(self) -> int:
        """Return number of currently active threshold events."""

        return len(self._active)