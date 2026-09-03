from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.data.models import MarketTick, OptionTick


class MarketDataProvider(ABC):
    """Common interface for market-data providers."""

    @abstractmethod
    def stream(self) -> AsyncIterator[OptionTick | MarketTick]:
        raise NotImplementedError