import io
import unittest
from unittest.mock import MagicMock, patch
import pandas as pd

from app.data.dhan import DhanMarketDataProvider
from app.data.models import OptionType
from app.data.scrip_master import DhanScripMaster


class TestDhanScripMaster(unittest.TestCase):
    def test_load_fno_universe_filtering(self):
        # Create mock scrip master CSV DataFrame
        mock_data = pd.DataFrame([
            {
                "SEM_EXM_EXCH_ID": "NSE",
                "SEM_SEGMENT": "D",
                "SEM_INSTRUMENT_NAME": "OPTSTK",
                "SEM_CUSTOM_SYMBOL": "RELIANCE 2800 CE",
                "SEM_SMST_SECURITY_ID": 100001,
                "SEM_OPTION_TYPE": "CE",
                "SEM_STRIKE_PRICE": 2800.0,
                "SEM_EXPIRY_DATE": "2026-10-29 15:30:00",
                "SEM_TRADING_SYMBOL": "RELIANCE-Oct2026-2800-CE",
            },
            {
                "SEM_EXM_EXCH_ID": "NSE",
                "SEM_SEGMENT": "D",
                "SEM_INSTRUMENT_NAME": "OPTSTK",
                "SEM_CUSTOM_SYMBOL": "RELIANCE 2800 PE",
                "SEM_SMST_SECURITY_ID": 100002,
                "SEM_OPTION_TYPE": "PE",
                "SEM_STRIKE_PRICE": 2800.0,
                "SEM_EXPIRY_DATE": "2026-10-29 15:30:00",
                "SEM_TRADING_SYMBOL": "RELIANCE-Oct2026-2800-PE",
            },
            {
                "SEM_EXM_EXCH_ID": "BSE",  # Should be excluded
                "SEM_SEGMENT": "E",
                "SEM_INSTRUMENT_NAME": "EQUITY",
                "SEM_CUSTOM_SYMBOL": "XYZ 100 CE",
                "SEM_SMST_SECURITY_ID": 999999,
                "SEM_OPTION_TYPE": "CE",
                "SEM_STRIKE_PRICE": 100.0,
                "SEM_EXPIRY_DATE": "2026-10-29",
                "SEM_TRADING_SYMBOL": "XYZ",
            }
        ])

        sm = DhanScripMaster()
        sm.df = mock_data
        subscriptions = sm.load_fno_universe(strikes_above_below=1)

        self.assertGreater(len(subscriptions), 0)
        self.assertEqual(subscriptions[0][0], 2)  # Segment code 2 (NSE_FNO)
        self.assertIn(100001, sm.security_id_map)
        self.assertEqual(sm.security_id_map[100001]["symbol"], "RELIANCE")
        self.assertEqual(sm.security_id_map[100001]["option_type"], OptionType.CE)


class TestDhanMarketDataProvider(unittest.TestCase):
    def setUp(self):
        self.provider = DhanMarketDataProvider(
            client_id="TEST_CLIENT",
            access_token="TEST_TOKEN",
        )
        self.provider.scrip_master.security_id_map[100001] = {
            "security_id": 100001,
            "symbol": "RELIANCE",
            "strike_price": 2800.0,
            "option_type": OptionType.CE,
            "expiry": "2026-10-29",
            "trading_symbol": "RELIANCE-Oct2026-2800-CE",
        }

    def test_missing_credentials_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                DhanMarketDataProvider(client_id="", access_token="")

    def test_handle_quote_packet(self):
        mock_queue = MagicMock()
        mock_loop = MagicMock()
        self.provider._queue = mock_queue
        self.provider._loop = mock_loop
        self.provider._running = True

        quote_packet = {
            "type": "Quote Data",
            "exchange_segment": 2,
            "security_id": 100001,
            "LTP": "125.50",
            "volume": 5000,
            "OI": 20000,
        }

        self.provider._handle_message(None, quote_packet)
        self.assertEqual(mock_loop.call_soon_threadsafe.call_count, 1)

    def test_handle_oi_packet(self):
        mock_queue = MagicMock()
        mock_loop = MagicMock()
        self.provider._queue = mock_queue
        self.provider._loop = mock_loop
        self.provider._running = True

        oi_packet = {
            "type": "OI Data",
            "exchange_segment": 2,
            "security_id": 100001,
            "OI": 45000,
        }

        self.provider._handle_message(None, oi_packet)
        self.assertEqual(self.provider._cached_oi[100001], 45000)
        # OI data packet alone doesn't trigger a new tick
        self.assertEqual(mock_loop.call_soon_threadsafe.call_count, 0)


if __name__ == "__main__":
    unittest.main()
