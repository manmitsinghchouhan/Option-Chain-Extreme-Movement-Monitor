import io
import os
from datetime import datetime
from pathlib import Path
import pandas as pd
import requests

from app.data.models import OptionType
from app.stocks import get_symbols

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
LOCAL_CACHE_PATH = Path(__file__).parent / "dhan_scrip_master.csv"


class DhanScripMaster:
    """
    Downloads, caches, and filters Dhan's daily Security Scrip Master
    for the 210 NSE F&O stocks and their active option strike contracts.
    """

    def __init__(self, cache_file: Path | str = LOCAL_CACHE_PATH) -> None:
        self.cache_path = Path(cache_file)
        self.df: pd.DataFrame | None = None
        self.security_id_map: dict[int, dict] = {}
        self.symbol_instruments: dict[str, list[dict]] = {}

    def fetch_master(self, force_refresh: bool = False) -> pd.DataFrame:
        """Download or load cached daily scrip master CSV."""
        # Use cache if less than 12 hours old
        if not force_refresh and self.cache_path.exists():
            mtime = datetime.fromtimestamp(self.cache_path.stat().st_mtime)
            if (datetime.now() - mtime).total_seconds() < 12 * 3600:
                self.df = pd.read_csv(self.cache_path, low_memory=False)
                return self.df

        response = requests.get(SCRIP_MASTER_URL, timeout=45)
        response.raise_for_status()

        self.df = pd.read_csv(
            io.StringIO(response.content.decode("utf-8", errors="ignore")),
            low_memory=False,
        )

        # Save to local cache
        self.df.to_csv(self.cache_path, index=False)
        return self.df

    def load_fno_universe(
        self,
        strikes_above_below: int = 6,
        force_refresh: bool = False,
    ) -> list[tuple[int, str, int]]:
        """
        Filter Scrip Master for all 210 F&O stocks and build subscription list.

        Returns:
            list of (exchange_segment, security_id, request_code)
        """
        if self.df is None:
            self.fetch_master(force_refresh=force_refresh)

        target_symbols = set(get_symbols())

        # Filter for NSE Derivatives Options (OPTSTK & OPTIDX)
        fno_df = self.df[
            (self.df["SEM_EXM_EXCH_ID"] == "NSE")
            & (self.df["SEM_SEGMENT"] == "D")
            & (self.df["SEM_INSTRUMENT_NAME"].isin(["OPTSTK", "OPTIDX"]))
        ].copy()

        # Extract underlying symbol from SEM_CUSTOM_SYMBOL (e.g. 'RELIANCE 2500 CE' -> 'RELIANCE')
        fno_df["UNDERLYING"] = (
            fno_df["SEM_CUSTOM_SYMBOL"].astype(str).str.split().str[0].str.strip()
        )
        matched_df = fno_df[fno_df["UNDERLYING"].isin(target_symbols)].copy()

        self.security_id_map.clear()
        self.symbol_instruments.clear()
        subscription_list = []

        now_str = datetime.now().strftime("%Y-%m-%d")

        # Build lookup for equity spot cash security IDs (NSE Cash segment = 'E')
        eq_df = self.df[
            (self.df["SEM_EXM_EXCH_ID"] == "NSE")
            & (self.df["SEM_SEGMENT"] == "E")
            & (self.df["SEM_TRADING_SYMBOL"].isin(target_symbols))
        ]
        equity_map = dict(zip(eq_df["SEM_TRADING_SYMBOL"], eq_df["SEM_SMST_SECURITY_ID"].astype(int)))

        # For each symbol, subscribe to equity cash spot tick and options around ATM
        for symbol, group in matched_df.groupby("UNDERLYING"):
            # Subscribe to underlying equity cash spot tick
            if symbol in equity_map:
                eq_sec_id = equity_map[symbol]
                if eq_sec_id not in self.security_id_map:
                    self.security_id_map[eq_sec_id] = {
                        "security_id": eq_sec_id,
                        "symbol": symbol,
                        "is_equity": True,
                        "trading_symbol": symbol,
                    }
                    subscription_list.append((1, str(eq_sec_id), 17))

            # Sort expiries ascending (only active future / today expiries)
            valid_expiries = [
                exp for exp in sorted(group["SEM_EXPIRY_DATE"].dropna().unique())
                if str(exp).split()[0] >= now_str
            ]
            if not valid_expiries:
                # If all expired, fallback to latest available in dataset
                valid_expiries = sorted(group["SEM_EXPIRY_DATE"].dropna().unique())
                if not valid_expiries:
                    continue

            nearest_expiry = valid_expiries[0]
            expiry_group = group[group["SEM_EXPIRY_DATE"] == nearest_expiry].copy()

            # Parse strikes
            expiry_group["STRIKE"] = pd.to_numeric(
                expiry_group["SEM_STRIKE_PRICE"], errors="coerce"
            )
            expiry_group = expiry_group.dropna(subset=["STRIKE"]).sort_values("STRIKE")

            unique_strikes = sorted(expiry_group["STRIKE"].unique())
            if not unique_strikes:
                continue

            # Select ATM strikes (middle slice)
            mid_idx = len(unique_strikes) // 2
            start_idx = max(0, mid_idx - strikes_above_below)
            end_idx = min(len(unique_strikes), mid_idx + strikes_above_below + 1)
            selected_strikes = set(unique_strikes[start_idx:end_idx])

            filtered_options = expiry_group[
                expiry_group["STRIKE"].isin(selected_strikes)
            ]

            symbol_items = []
            for _, row in filtered_options.iterrows():
                sec_id = int(row["SEM_SMST_SECURITY_ID"])
                opt_type_str = str(row["SEM_OPTION_TYPE"]).strip().upper()
                opt_type = OptionType.CE if opt_type_str == "CE" else OptionType.PE
                strike_val = float(row["STRIKE"])
                expiry_str = str(row["SEM_EXPIRY_DATE"]).split()[0]

                meta = {
                    "security_id": sec_id,
                    "symbol": symbol,
                    "strike_price": strike_val,
                    "option_type": opt_type,
                    "expiry": expiry_str,
                    "trading_symbol": str(row["SEM_TRADING_SYMBOL"]),
                }

                self.security_id_map[sec_id] = meta
                symbol_items.append(meta)

                # NSE_FNO code is 2, Quote request code is 17
                subscription_list.append((2, str(sec_id), 17))

            self.symbol_instruments[symbol] = symbol_items

        return subscription_list

    def get_equity_security_id(self, symbol: str) -> int | None:
        """Find NSE equity cash security ID for a symbol."""
        if self.df is None:
            self.fetch_master()

        match = self.df[
            (self.df["SEM_EXM_EXCH_ID"] == "NSE")
            & (self.df["SEM_SEGMENT"] == "E")
            & (self.df["SEM_TRADING_SYMBOL"] == symbol)
        ]
        if not match.empty:
            return int(match["SEM_SMST_SECURITY_ID"].iloc[0])
        return None
