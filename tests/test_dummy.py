import asyncio
from datetime import datetime

from app.data.dummy import DummyMarketDataProvider, MarketTick
from app.stocks import get_symbols


def test_market_tick_contains_required_data():
    tick = MarketTick(
        symbol="RELIANCE",
        price=1500.50,
        timestamp=datetime.now(),
        volume=10_000,
    )

    assert tick.symbol == "RELIANCE"
    assert tick.price == 1500.50
    assert isinstance(tick.timestamp, datetime)
    assert tick.volume == 10_000


def test_dummy_provider_loads_all_stocks():
    provider = DummyMarketDataProvider()

    assert len(provider.symbols) == 210
    assert provider.symbols == get_symbols()


def test_dummy_provider_has_starting_price_for_every_stock():
    provider = DummyMarketDataProvider()

    assert len(provider.prices) == 210
    assert all(price > 0 for price in provider.prices.values())


def test_dummy_provider_generates_normal_tick():
    provider = DummyMarketDataProvider()

    symbol = provider.symbols[0]

    tick = provider._generate_tick(symbol)

    assert isinstance(tick, MarketTick)
    assert tick.symbol == symbol
    assert tick.price > 0
    assert isinstance(tick.timestamp, datetime)
    assert tick.volume > 0


def test_dummy_provider_can_generate_extreme_movement():
    provider = DummyMarketDataProvider(
        extreme_test_symbol="RELIANCE"
    )

    starting_price = provider.prices["RELIANCE"]

    prices = []

    for _ in range(10):
        tick = provider._generate_tick("RELIANCE")
        prices.append(tick.price)

    assert prices[0] == round(starting_price, 2)

    assert prices[-1] == round(starting_price * 0.20, 2)


from app.data.models import OptionTick, OptionType


def test_dummy_stream_produces_ticks():
    provider = DummyMarketDataProvider(
        update_interval_seconds=0
    )

    async def get_first_tick():
        stream = provider.stream()

        return await anext(stream)

    tick = asyncio.run(get_first_tick())

    assert isinstance(tick, (MarketTick, OptionTick))
    assert tick.symbol in get_symbols()
    assert tick.price > 0


def test_dummy_generates_option_strikes_for_all_stocks():
    provider = DummyMarketDataProvider()
    for symbol in provider.symbols[:10]:
        strikes = provider.get_strikes_for_symbol(symbol)
        assert len(strikes) > 0
        assert all(s > 0 for s in strikes)


def test_dummy_inject_extreme_spike():
    provider = DummyMarketDataProvider()
    key = provider.inject_extreme_spike(symbol="TCS", percentage_change=80.0)
    assert "TCS" in key
    assert key in provider._pending_spikes