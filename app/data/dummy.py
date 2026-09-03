import asyncio
import random
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

from app.data.base import MarketDataProvider
from app.data.models import MarketTick, OptionTick, OptionType
from app.stocks import get_symbols


def get_strike_interval(price: float) -> float:
    """Determine realistic NSE strike interval based on stock price."""
    if price < 150:
        return 2.5
    if price < 300:
        return 5.0
    if price < 750:
        return 10.0
    if price < 1500:
        return 20.0
    if price < 3000:
        return 50.0
    return 100.0


class DummyMarketDataProvider(MarketDataProvider):
    """
    Simulates a real-time Option Chain market-data stream for 210 F&O stocks.
    Generates Call (CE) and Put (PE) strike ladders around ATM.
    Supports autonomous multi-stock extreme surge/plunge events.
    """

    def __init__(
        self,
        update_interval_seconds: float = 1.0,
        strikes_above_below: int = 4,
        extreme_test_symbol: str | None = None,
        enable_random_spikes: bool = True,
        expiry_date: str = "2026-09-24",
        **kwargs,
    ) -> None:
        self.update_interval_seconds = update_interval_seconds
        self.strikes_above_below = strikes_above_below
        self.symbols = get_symbols()
        self.expiry_date = expiry_date
        self.enable_random_spikes = enable_random_spikes


        # Initial spot prices for 210 stocks
        random.seed(42)  # Consistent baseline across runs
        self.spot_prices: dict[str, float] = {
            symbol: round(random.uniform(150.0, 3500.0), 2)
            for symbol in self.symbols
        }

        # Baseline option premiums dictionary: instrument_key -> float
        self.premiums: dict[str, float] = {}
        self.volumes: dict[str, int] = {}
        self.open_interests: dict[str, int] = {}

        # Extreme movement test controls
        self.extreme_test_symbol = extreme_test_symbol
        self._extreme_step = 0
        self.extreme_starting_premiums: dict[str, float] = {}
        self._simulation_timestamp = datetime.now()

        # Dynamic manual injection trigger: (instrument_key, multiplier)
        self._pending_spikes: dict[str, float] = {}

        # Precalculated fixed strike lists for all symbols
        self.strikes: dict[str, list[float]] = {}

        self._initialize_option_chain()


    @property
    def prices(self) -> dict[str, float]:
        """Backward compatibility alias for spot prices."""
        return self.spot_prices

    def _initialize_option_chain(self) -> None:
        """Create baseline Option Chain strikes for all stocks."""
        for symbol in self.symbols:
            spot = self.spot_prices[symbol]
            interval = get_strike_interval(spot)
            atm_strike = round(spot / interval) * interval

            symbol_strikes = []
            for offset in range(-self.strikes_above_below, self.strikes_above_below + 1):
                strike = atm_strike + (offset * interval)
                if strike > 0:
                    symbol_strikes.append(strike)

            self.strikes[symbol] = sorted(symbol_strikes)

            for strike in self.strikes[symbol]:
                # Call (CE) premium calculation
                ce_intrinsic = max(0.0, spot - strike)
                ce_time_value = max(spot * 0.025 - abs(spot - strike) * 0.08, 4.0)
                ce_premium = round(max(ce_intrinsic + ce_time_value, 2.5), 2)

                ce_key = f"{symbol}_{self.expiry_date}_{strike:.0f}_CE"
                self.premiums[ce_key] = ce_premium
                self.volumes[ce_key] = random.randint(1_000, 50_000)
                self.open_interests[ce_key] = random.randint(10_000, 200_000)

                # Put (PE) premium calculation
                pe_intrinsic = max(0.0, strike - spot)
                pe_time_value = max(spot * 0.025 - abs(strike - spot) * 0.08, 4.0)
                pe_premium = round(max(pe_intrinsic + pe_time_value, 2.5), 2)

                pe_key = f"{symbol}_{self.expiry_date}_{strike:.0f}_PE"
                self.premiums[pe_key] = pe_premium
                self.volumes[pe_key] = random.randint(1_000, 50_000)
                self.open_interests[pe_key] = random.randint(10_000, 200_000)

                if symbol == self.extreme_test_symbol:
                    self.extreme_starting_premiums[ce_key] = ce_premium
                    self.extreme_starting_premiums[pe_key] = pe_premium

    def get_strikes_for_symbol(self, symbol: str) -> list[float]:
        """Return list of generated strike prices for a symbol."""
        if symbol in self.strikes:
            return self.strikes[symbol]
        spot = self.spot_prices.get(symbol, 1000.0)
        interval = get_strike_interval(spot)
        atm_strike = round(spot / interval) * interval
        return sorted([
            atm_strike + (offset * interval)
            for offset in range(-self.strikes_above_below, self.strikes_above_below + 1)
            if atm_strike + (offset * interval) > 0
        ])

    def _generate_tick(self, symbol: str) -> MarketTick:
        """Backward compatibility: Generate one underlying stock tick."""
        if symbol == self.extreme_test_symbol:
            starting = self.spot_prices[symbol]
            movement_levels = [1.00, 0.95, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30, 0.20]
            level = movement_levels[min(self._extreme_step, len(movement_levels) - 1)]
            self._extreme_step += 1
            new_spot = round(max(starting * level, 0.01), 2)
        else:
            spot = self.spot_prices[symbol]
            drift = random.uniform(-0.5, 0.5)
            new_spot = round(max(spot * (1 + drift / 100), 0.01), 2)
            self.spot_prices[symbol] = new_spot

        return MarketTick(
            symbol=symbol,
            price=new_spot,
            timestamp=self._simulation_timestamp,
            volume=random.randint(1000, 50000),
        )


    def inject_extreme_spike(
        self,
        symbol: str | None = None,
        strike: float | None = None,
        option_type: OptionType | None = None,
        percentage_change: float = 75.0,
    ) -> str:
        """Manually inject a sudden surge or drop into a specific or random option contract."""
        if symbol is None:
            symbol = random.choice(self.symbols)
        strikes = self.get_strikes_for_symbol(symbol)
        if strike is None:
            strike = strikes[len(strikes) // 2]
        if option_type is None:
            option_type = random.choice([OptionType.CE, OptionType.PE])

        key = f"{symbol}_{self.expiry_date}_{strike:.0f}_{option_type.value}"
        multiplier = 1.0 + (percentage_change / 100.0)
        self._pending_spikes[key] = multiplier
        return key

    def _generate_next_premium(
        self,
        key: str,
        symbol: str,
        strike: float,
        opt_type: OptionType,
    ) -> float:
        """Simulate next tick premium with normal fluctuation or extreme triggers."""
        current_premium = self.premiums.get(key, 10.0)

        # 1. Check for manual injected spike
        if key in self._pending_spikes:
            multiplier = self._pending_spikes.pop(key)
            new_premium = round(max(current_premium * multiplier, 0.05), 2)
            self.premiums[key] = new_premium
            return new_premium

        # 2. Check for controlled demo test symbol (e.g. RELIANCE CE surge / PE plunge)
        if symbol == self.extreme_test_symbol and opt_type == OptionType.CE:
            base = self.extreme_starting_premiums.get(key, current_premium)
            levels = [1.0, 1.15, 1.35, 1.62, 1.74, 1.85, 1.90]
            step_idx = min(self._extreme_step, len(levels) - 1)
            new_premium = round(max(base * levels[step_idx], 0.05), 2)
            self.premiums[key] = new_premium
            return new_premium

        # 3. Normal option market fluctuation (±1.5%)
        pct_change = random.uniform(-1.5, 1.5)
        new_premium = current_premium * (1 + pct_change / 100)
        new_premium = round(max(new_premium, 0.5), 2)
        self.premiums[key] = new_premium
        return new_premium

    def generate_option_ticks(self) -> list[OptionTick]:
        """Generate a complete snapshot batch of option ticks for all 210 stocks."""
        timestamp = self._simulation_timestamp
        ticks: list[OptionTick] = []

        # Organic autonomous spike generation (every ~3-4 batches)
        if self.enable_random_spikes and self._extreme_step > 0 and self._extreme_step % 3 == 0:
            if random.random() < 0.85:
                random_stock = random.choice(self.symbols)
                random_pct = random.choice([65.0, 72.0, 82.0, -68.0, -78.0])
                self.inject_extreme_spike(symbol=random_stock, percentage_change=random_pct)

        for symbol in self.symbols:
            spot = self.spot_prices[symbol]
            spot_drift = random.uniform(-0.15, 0.15)
            spot = round(spot * (1 + spot_drift / 100), 2)
            self.spot_prices[symbol] = spot

            strikes = self.get_strikes_for_symbol(symbol)
            for strike in strikes:
                for opt_type in (OptionType.CE, OptionType.PE):
                    key = f"{symbol}_{self.expiry_date}_{strike:.0f}_{opt_type.value}"
                    premium = self._generate_next_premium(key, symbol, strike, opt_type)

                    vol_change = random.randint(50, 1500)
                    self.volumes[key] = self.volumes.get(key, 1000) + vol_change

                    ticks.append(
                        OptionTick(
                            symbol=symbol,
                            strike_price=strike,
                            option_type=opt_type,
                            expiry=self.expiry_date,
                            premium=premium,
                            timestamp=timestamp,
                            volume=self.volumes[key],
                            open_interest=self.open_interests.get(key, 10000),
                            underlying_price=spot,
                        )
                    )

        # Increment extreme step & simulation time
        self._extreme_step += 1
        self._simulation_timestamp += timedelta(minutes=5)

        return ticks

    async def stream(self) -> AsyncIterator[OptionTick]:
        """Continuous async stream yielding option ticks."""
        while True:
            ticks = self.generate_option_ticks()
            for tick in ticks:
                yield tick
            await asyncio.sleep(self.update_interval_seconds)
