import asyncio
import logging
import os
import queue
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

from app.data.base import MarketDataProvider
from app.data.models import MarketTick, OptionTick, OptionType
from app.data.scrip_master import DhanScripMaster

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class DhanMarketDataProvider(MarketDataProvider):
    """
    Live real-time market data provider connecting to DhanHQ v2 WebSocket feed
    for all 210 NSE F&O stocks and their option strike contracts.
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
        strikes_above_below: int = 6,
    ) -> None:
        self.client_id = client_id or os.getenv("DHAN_CLIENT_ID", "")
        self.access_token = access_token or os.getenv("DHAN_ACCESS_TOKEN", "")

        self.strikes_above_below = strikes_above_below
        self.scrip_master = DhanScripMaster()
        self._running = False
        self._feed = None
        self.live_queue = queue.Queue()
        self._queue: Optional[asyncio.Queue[OptionTick]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._cached_oi: dict[int, int] = {}
        self._cached_spot: dict[str, float] = {}
        self.last_error: Optional[str] = None

        if not self.client_id or not self.access_token:
            self.last_error = "🔑 DhanHQ credentials missing. Please set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in Secrets or sidebar."

    def _handle_message(self, instance, packet: dict) -> None:
        """Callback invoked by MarketFeed background thread on every tick."""
        if not self._running or self._queue is None or self._loop is None:
            return

        try:
            if not isinstance(packet, dict):
                return

            p_type = packet.get("type")
            sec_id = packet.get("security_id")
            if not sec_id:
                return

            sec_id = int(sec_id)

            # Store OI updates
            if p_type == "OI Data":
                oi_val = packet.get("OI", 0)
                try:
                    self._cached_oi[sec_id] = int(oi_val)
                except (ValueError, TypeError):
                    pass
                return

            # Handle Quote Data, Full Data, and Ticker Data
            if p_type in ("Quote Data", "Full Data", "Ticker Data"):
                meta = self.scrip_master.security_id_map.get(sec_id)
                if not meta:
                    return

                ltp_raw = packet.get("LTP")
                if ltp_raw is None:
                    return

                try:
                    ltp = float(ltp_raw)
                except (ValueError, TypeError):
                    return

                if ltp <= 0:
                    return

                volume_raw = packet.get("volume", 0)
                try:
                    volume = int(volume_raw)
                except (ValueError, TypeError):
                    volume = 0

                oi = packet.get("OI", self._cached_oi.get(sec_id, 0))
                try:
                    oi = int(oi)
                except (ValueError, TypeError):
                    oi = 0

                tick = OptionTick(
                    symbol=meta["symbol"],
                    strike_price=meta["strike_price"],
                    option_type=meta["option_type"],
                    expiry=meta["expiry"],
                    premium=ltp,
                    timestamp=datetime.now(),
                    volume=volume,
                    open_interest=oi,
                    underlying_price=0.0,
                )

                self._loop.call_soon_threadsafe(self._queue.put_nowait, tick)

        except Exception as e:
            logger.error("Error processing Dhan packet: %s", e)

    async def stream(self) -> AsyncIterator[OptionTick]:
        """
        Connects to DhanHQ WebSocket feed and yields OptionTick items asynchronously.
        """
        from dhanhq import DhanContext, MarketFeed

        logger.info("Initializing Dhan Scrip Master for F&O Universe...")
        instruments = self.scrip_master.load_fno_universe(
            strikes_above_below=self.strikes_above_below
        )
        logger.info(
            "Subscribing to %d option contracts across 210 F&O stocks...",
            len(instruments),
        )

        ctx = DhanContext(client_id=self.client_id, access_token=self.access_token)
        self._queue = asyncio.Queue()
        self._loop = asyncio.get_running_loop()
        self._running = True

        self._feed = MarketFeed(
            dhan_context=ctx,
            instruments=instruments,
            version="v2",
            on_message=self._handle_message,
        )

        # Start Dhan feed thread
        self._feed.start()
        logger.info("DhanHQ WebSocket connection started.")

        try:
            while self._running:
                try:
                    # Wait for next tick with timeout to check running flag
                    tick = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                    self._queue.task_done()
                    yield tick
                except asyncio.TimeoutError:
                    continue
        finally:
            self.stop()

    def fetch_real_option_chain(self, symbol: str) -> dict | None:
        """
        Fetch real-world option chain snapshot for any F&O stock directly from DhanHQ REST API.
        Returns underlying spot, expiry, and strike chain matrix with real market LTP, OI, and volume.
        """
        try:
            from dhanhq import DhanContext, dhanhq

            sec_id = self.scrip_master.get_equity_security_id(symbol)
            if not sec_id:
                logger.warning("Could not find equity Security ID for %s", symbol)
                return None

            ctx = DhanContext(client_id=self.client_id, access_token=self.access_token)
            dhan = dhanhq(ctx)

            exp_res = dhan.expiry_list(sec_id, "NSE_FNO")
            if not exp_res or exp_res.get("status") != "success":
                err_msg = str(exp_res.get("remarks") or exp_res.get("error") or "") if exp_res else "No response"
                if "auth" in err_msg.lower() or "token" in err_msg.lower() or not exp_res:
                    self.last_error = "🔑 DhanHQ Access Token Expired or Invalid. Please generate a new access token from dhanhq.co and update your .env file."
                return None

            exp_data = exp_res.get("data", {})
            if isinstance(exp_data, dict) and "data" in exp_data:
                expiries = exp_data["data"]
            elif isinstance(exp_data, list):
                expiries = exp_data
            else:
                return None

            if not expiries:
                return None

            nearest_expiry = expiries[0]
            chain_res = dhan.option_chain(sec_id, "NSE_FNO", nearest_expiry)
            if not chain_res or chain_res.get("status") != "success":
                return None

            # Clear last error on successful fetch
            self.last_error = None

            raw_data = chain_res.get("data", {}).get("data", {})
            spot_price = float(raw_data.get("last_price", 0.0) or 0.0)
            raw_oc = raw_data.get("oc", {})

            strikes_data = []
            for strike_str, strike_info in raw_oc.items():
                strike_val = float(strike_str)
                ce_info = strike_info.get("ce", {})
                pe_info = strike_info.get("pe", {})

                strikes_data.append({
                    "strike": strike_val,
                    "ce_sec_id": ce_info.get("security_id"),
                    "ce_ltp": float(ce_info.get("last_price", 0.0) or 0.0),
                    "ce_prev_close": float(ce_info.get("previous_close_price", 0.0) or 0.0),
                    "ce_volume": int(ce_info.get("volume", 0) or 0),
                    "ce_oi": int(ce_info.get("oi", 0) or 0),
                    "pe_sec_id": pe_info.get("security_id"),
                    "pe_ltp": float(pe_info.get("last_price", 0.0) or 0.0),
                    "pe_prev_close": float(pe_info.get("previous_close_price", 0.0) or 0.0),
                    "pe_volume": int(pe_info.get("volume", 0) or 0),
                    "pe_oi": int(pe_info.get("oi", 0) or 0),
                })

            strikes_data.sort(key=lambda x: x["strike"])

            # Fallback for spot price when market is closed or after-hours
            if spot_price <= 0:
                spot_price = float(self._cached_spot.get(symbol, 0.0))

            if spot_price <= 0 and strikes_data:
                active_strikes = [
                    s["strike"] for s in strikes_data
                    if (s["ce_ltp"] > 0 or s["pe_ltp"] > 0 or s["ce_prev_close"] > 0 or s["ce_oi"] > 0 or s["pe_oi"] > 0)
                ]
                if active_strikes:
                    spot_price = active_strikes[len(active_strikes) // 2]
                else:
                    spot_price = strikes_data[len(strikes_data) // 2]["strike"]

            # Filter strikes around ATM (closest to spot price)
            if spot_price > 0 and strikes_data:
                closest_strike = min(strikes_data, key=lambda x: abs(x["strike"] - spot_price))["strike"]
                closest_idx = [i for i, x in enumerate(strikes_data) if x["strike"] == closest_strike][0]
                start_i = max(0, closest_idx - 6)
                end_i = min(len(strikes_data), closest_idx + 7)
                strikes_data = strikes_data[start_i:end_i]

            # Dynamically register security IDs and subscribe to live feed if active
            new_sub_instruments = []
            for item in strikes_data:
                strike_val = item["strike"]
                if item.get("ce_sec_id"):
                    ce_id = int(item["ce_sec_id"])
                    self.scrip_master.security_id_map[ce_id] = {
                        "security_id": ce_id,
                        "symbol": symbol,
                        "strike_price": strike_val,
                        "option_type": OptionType.CE,
                        "expiry": nearest_expiry,
                        "trading_symbol": f"{symbol} {strike_val:.0f} CE",
                    }
                    new_sub_instruments.append((2, str(ce_id), 17))

                if item.get("pe_sec_id"):
                    pe_id = int(item["pe_sec_id"])
                    self.scrip_master.security_id_map[pe_id] = {
                        "security_id": pe_id,
                        "symbol": symbol,
                        "strike_price": strike_val,
                        "option_type": OptionType.PE,
                        "expiry": nearest_expiry,
                        "trading_symbol": f"{symbol} {strike_val:.0f} PE",
                    }
                    new_sub_instruments.append((2, str(pe_id), 17))

            # Also register and dynamically subscribe equity cash security ID for live spot updates
            eq_sec_id = self.scrip_master.get_equity_security_id(symbol)
            if eq_sec_id:
                self.scrip_master.security_id_map[int(eq_sec_id)] = {
                    "security_id": int(eq_sec_id),
                    "symbol": symbol,
                    "is_equity": True,
                    "trading_symbol": symbol,
                }
                new_sub_instruments.append((1, str(eq_sec_id), 17))

            if self._feed and new_sub_instruments:
                try:
                    self._feed.subscribe_symbols(new_sub_instruments)
                    logger.info("Dynamically subscribed to %d active contracts for %s", len(new_sub_instruments), symbol)
                except Exception as e:
                    logger.debug("Could not dynamically subscribe symbols: %s", e)

            return {
                "symbol": symbol,
                "spot_price": spot_price,
                "expiry": nearest_expiry,
                "strikes": strikes_data,
            }

        except Exception as e:
            logger.error("Error fetching real option chain for %s: %s", symbol, e)
            self.last_error = "🔑 DhanHQ Access Token Expired or Invalid. Please generate a new access token from dhanhq.co and update your .env file."
            return None

    def start_background_feed(self, on_tick_callback) -> None:
        """Start DhanHQ MarketFeed in a dedicated background thread with real-time callback."""
        if self._running:
            return

        from dhanhq import DhanContext, MarketFeed
        import threading

        logger.info("Initializing Dhan Scrip Master for background live feed...")
        instruments = self.scrip_master.load_fno_universe(
            strikes_above_below=self.strikes_above_below
        )
        logger.info("Subscribing to %d option & equity contracts...", len(instruments))

        ctx = DhanContext(client_id=self.client_id, access_token=self.access_token)
        self._running = True

        def _packet_handler(instance, packet: dict):
            if not self._running or not isinstance(packet, dict):
                return
            try:
                p_type = packet.get("type")
                sec_id = packet.get("security_id")
                if not sec_id:
                    return
                sec_id = int(sec_id)

                if p_type == "OI Data":
                    oi_val = packet.get("OI", 0)
                    try:
                        self._cached_oi[sec_id] = int(oi_val)
                    except (ValueError, TypeError):
                        pass
                    return

                if p_type in ("Quote Data", "Full Data", "Ticker Data"):
                    meta = self.scrip_master.security_id_map.get(sec_id)
                    if not meta:
                        return
                    ltp_raw = packet.get("LTP")
                    if ltp_raw is None:
                        return
                    ltp = float(ltp_raw)
                    if ltp <= 0:
                        return
                    vol = int(packet.get("volume", 0) or 0)

                    if meta.get("is_equity"):
                        self._cached_spot[meta["symbol"]] = ltp
                        eq_tick = MarketTick(
                            symbol=meta["symbol"],
                            price=ltp,
                            timestamp=datetime.now(),
                            volume=vol,
                        )
                        self.live_queue.put_nowait(eq_tick)
                        if on_tick_callback:
                            on_tick_callback(eq_tick)
                        return

                    oi = int(packet.get("OI", self._cached_oi.get(sec_id, 0)) or 0)
                    spot_val = self._cached_spot.get(meta["symbol"], 0.0)

                    tick = OptionTick(
                        symbol=meta["symbol"],
                        strike_price=meta["strike_price"],
                        option_type=meta["option_type"],
                        expiry=meta["expiry"],
                        premium=ltp,
                        timestamp=datetime.now(),
                        volume=vol,
                        open_interest=oi,
                        underlying_price=spot_val,
                    )
                    self.live_queue.put_nowait(tick)
                    if on_tick_callback:
                        on_tick_callback(tick)
            except Exception as e:
                logger.error("Error in background feed packet handler: %s", e)

        self._feed = MarketFeed(
            dhan_context=ctx,
            instruments=instruments,
            version="v2",
            on_message=_packet_handler,
        )

        def _run_feed_safely():
            try:
                self._feed.run()
            except Exception as e:
                err_str = str(e).lower()
                if "connectionclosed" in type(e).__name__.lower() or "close frame" in err_str or "unauthorized" in err_str:
                    msg = "🔑 DhanHQ Access Token Expired or Invalid. Please generate a new access token from dhanhq.co and update your .env file."
                    logger.warning(msg)
                    self.last_error = msg
                else:
                    logger.warning("DhanHQ WebSocket disconnected: %s", e)
                    self.last_error = f"⚠️ DhanHQ WebSocket disconnected: {e}"
                self._running = False

        thread = threading.Thread(target=_run_feed_safely, daemon=True)
        thread.start()
        logger.info("DhanHQ Live WebSocket background thread started safely.")

    def stop(self) -> None:
        """Disconnect and stop live stream feed safely."""
        self._running = False
        feed_to_close = self._feed
        self._feed = None
        if feed_to_close:
            try:
                feed_to_close.close_connection()
            except Exception as e:
                logger.warning("Error closing Dhan feed: %s", e)
        logger.info("DhanMarketDataProvider stopped.")
