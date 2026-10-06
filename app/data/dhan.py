import asyncio
import logging
import os
import queue
from collections.abc import AsyncIterator
from datetime import datetime, timezone, timedelta
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))

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
    for all 213 NSE F&O stocks and their option strike contracts.
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
        strikes_above_below: int = 5,
        index_strikes_above_below: int = 10,
    ) -> None:
        raw_cid = client_id or os.getenv("DHAN_CLIENT_ID", "")
        raw_tok = access_token or os.getenv("DHAN_ACCESS_TOKEN", "")
        self.client_id = str(raw_cid).strip().strip('"').strip("'")
        self.access_token = str(raw_tok).strip().strip('"').strip("'")

        self.strikes_above_below = strikes_above_below
        self.index_strikes_above_below = index_strikes_above_below
        self.scrip_master = DhanScripMaster()
        self._running = False
        self._feed = None
        self.live_queue = queue.Queue()
        self._queue: Optional[asyncio.Queue[OptionTick]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._cached_oi: dict[int, int] = {}
        self._cached_spot: dict[str, float] = {}
        self._cached_prev_close: dict[int, float] = {}
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

            # Store Previous Close updates
            if p_type == "Previous Close":
                p_close = packet.get("prev_close")
                if p_close is not None:
                    try:
                        self._cached_prev_close[sec_id] = float(p_close)
                    except (ValueError, TypeError):
                        pass
                return

            # Handle Quote Data, Full Data, and Ticker Data
            if p_type in ("Quote Data", "Full Data", "Ticker Data"):
                p_close_raw = packet.get("close") or packet.get("prev_close")
                if p_close_raw is not None:
                    try:
                        self._cached_prev_close[sec_id] = float(p_close_raw)
                    except (ValueError, TypeError):
                        pass
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
                    timestamp=datetime.now(IST),
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
            strikes_above_below=self.strikes_above_below,
            index_strikes_above_below=self.index_strikes_above_below,
        )
        logger.info(
            "Subscribing to %d option contracts across 213 F&O stocks & indices...",
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
        Fetch real-world option chain snapshot for any F&O stock or index.
        Uses cached Scrip Master instrument definitions combined with DhanHQ REST API and live WebSocket ticks.
        """
        try:
            if self.scrip_master.df is None or not self.scrip_master.symbol_instruments:
                self.scrip_master.load_fno_universe(
                    strikes_above_below=self.strikes_above_below,
                    index_strikes_above_below=self.index_strikes_above_below,
                )

            instruments = self.scrip_master.symbol_instruments.get(symbol, [])
            if not instruments:
                logger.warning("No option instruments found in Scrip Master for %s", symbol)
                return None

            nearest_expiry = instruments[0].get("expiry", "")
            strikes_map: dict[float, dict] = {}
            for inst in instruments:
                strike = float(inst["strike_price"])
                if strike not in strikes_map:
                    strikes_map[strike] = {
                        "strike": strike,
                        "ce_sec_id": None,
                        "ce_ltp": 0.0,
                        "ce_prev_close": 0.0,
                        "ce_volume": 0,
                        "ce_oi": 0,
                        "pe_sec_id": None,
                        "pe_ltp": 0.0,
                        "pe_prev_close": 0.0,
                        "pe_volume": 0,
                        "pe_oi": 0,
                    }
                if inst["option_type"] == OptionType.CE:
                    strikes_map[strike]["ce_sec_id"] = inst["security_id"]
                elif inst["option_type"] == OptionType.PE:
                    strikes_map[strike]["pe_sec_id"] = inst["security_id"]

            strikes_data = sorted(strikes_map.values(), key=lambda x: x["strike"])
            spot_price = float(self._cached_spot.get(symbol, 0.0))

            # Optional: Enforce fresh REST data from DhanHQ if client credentials present
            if self.client_id and self.access_token:
                try:
                    from dhanhq import DhanContext, dhanhq

                    sec_id = self.scrip_master.get_equity_security_id(symbol)
                    if sec_id:
                        ctx = DhanContext(client_id=self.client_id, access_token=self.access_token)
                        dhan = dhanhq(ctx)

                        underlying_seg = "IDX_I" if symbol in ("NIFTY", "SENSEX") else "NSE_EQ"
                        exp_res = dhan.expiry_list(sec_id, underlying_seg)
                        if not exp_res or exp_res.get("status") != "success":
                            alt_seg = "BSE_FNO" if symbol == "SENSEX" else "NSE_FNO"
                            exp_res = dhan.expiry_list(sec_id, alt_seg)

                        if exp_res and exp_res.get("status") == "success":
                            exp_data = exp_res.get("data", {})
                            expiries = exp_data.get("data", []) if isinstance(exp_data, dict) else (exp_data if isinstance(exp_data, list) else [])
                            if expiries:
                                chain_res = dhan.option_chain(sec_id, underlying_seg, expiries[0])
                                if not chain_res or chain_res.get("status") != "success":
                                    alt_seg = "BSE_FNO" if symbol == "SENSEX" else "NSE_FNO"
                                    chain_res = dhan.option_chain(sec_id, alt_seg, expiries[0])

                                if chain_res and chain_res.get("status") == "success":
                                    raw_data = chain_res.get("data", {}).get("data", {})
                                    spot_val = float(raw_data.get("last_price", 0.0) or 0.0)
                                    if spot_val > 0:
                                        spot_price = spot_val
                                    raw_oc = raw_data.get("oc", {})
                                    for s_item in strikes_data:
                                        s_k = str(int(s_item["strike"])) if s_item["strike"].is_integer() else str(s_item["strike"])
                                        s_entry = raw_oc.get(s_k) or raw_oc.get(f"{s_item['strike']:.2f}") or raw_oc.get(f"{s_item['strike']:.1f}")
                                        if s_entry:
                                            ce_info = s_entry.get("ce", {})
                                            pe_info = s_entry.get("pe", {})
                                            if ce_info:
                                                s_item["ce_ltp"] = float(ce_info.get("last_price", 0.0) or 0.0)
                                                s_item["ce_prev_close"] = float(ce_info.get("previous_close_price", 0.0) or 0.0)
                                                s_item["ce_volume"] = int(ce_info.get("volume", 0) or 0)
                                                s_item["ce_oi"] = int(ce_info.get("oi", 0) or 0)
                                            if pe_info:
                                                s_item["pe_ltp"] = float(pe_info.get("last_price", 0.0) or 0.0)
                                                s_item["pe_prev_close"] = float(pe_info.get("previous_close_price", 0.0) or 0.0)
                                                s_item["pe_volume"] = int(pe_info.get("volume", 0) or 0)
                                                s_item["pe_oi"] = int(pe_info.get("oi", 0) or 0)
                except Exception as e:
                    logger.debug("REST option chain query skipped: %s", e)

            # Clear error on successful structure building
            self.last_error = None

            return {
                "symbol": symbol,
                "spot_price": spot_price,
                "expiry": nearest_expiry,
                "strikes": strikes_data,
            }

        except Exception as e:
            logger.error("Error generating option chain for %s: %s", symbol, e)
            return None

    def verify_credentials(self) -> tuple[bool, str]:
        """Validate DhanHQ credentials via lightweight REST call."""
        if not self.client_id or not self.access_token:
            err = "🔑 DhanHQ credentials missing. Please set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in Secrets or sidebar."
            self.last_error = err
            return False, err
        try:
            from dhanhq import DhanContext, dhanhq
            ctx = DhanContext(client_id=self.client_id, access_token=self.access_token)
            dhan = dhanhq(ctx)
            res = dhan.get_fund_limits()
            if isinstance(res, dict):
                status = str(res.get("status", "")).lower()
                if status == "success":
                    self.last_error = None
                    return True, "Authenticated"
                remarks = res.get("remarks") or {}
                msg = remarks.get("error_message") or remarks.get("message") or res.get("message") or ""
                if any(k in str(msg).lower() for k in ("token", "auth", "unauthor", "invalid")):
                    err = "🔑 DhanHQ Access Token Expired or Invalid. Please generate a new access token from dhanhq.co and update your .env file or Secrets."
                    self.last_error = err
                    return False, err
            return True, "Connected"
        except Exception as e:
            err_str = str(e).lower()
            if any(k in err_str for k in ("401", "403", "unauthor", "token")):
                err = "🔑 DhanHQ Access Token Expired or Invalid. Please generate a new access token from dhanhq.co and update your .env file or Secrets."
                self.last_error = err
                return False, err
            return True, f"Status check skipped: {e}"

    def start_background_feed(self, on_tick_callback) -> None:
        """Start DhanHQ MarketFeed in a dedicated background thread with real-time callback."""
        if self._running:
            return

        from dhanhq import DhanContext, MarketFeed
        import threading

        self._running = True
        self.last_error = None

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

                if p_type == "Previous Close":
                    p_close = packet.get("prev_close")
                    if p_close is not None:
                        try:
                            self._cached_prev_close[sec_id] = float(p_close)
                        except (ValueError, TypeError):
                            pass
                    return

                if p_type in ("Quote Data", "Full Data", "Ticker Data"):
                    p_close_raw = packet.get("close") or packet.get("prev_close")
                    if p_close_raw is not None:
                        try:
                            self._cached_prev_close[sec_id] = float(p_close_raw)
                        except (ValueError, TypeError):
                            pass
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
                            timestamp=datetime.now(IST),
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
                        timestamp=datetime.now(IST),
                        volume=vol,
                        open_interest=oi,
                        underlying_price=spot_val,
                    )
                    self.live_queue.put_nowait(tick)
                    if on_tick_callback:
                        on_tick_callback(tick)
            except Exception as e:
                logger.error("Error in background feed packet handler: %s", e)

        def _on_close(instance):
            logger.info("DhanHQ WebSocket connection closed.")

        def _on_error(instance, error):
            err_str = str(error).lower()
            err_type = type(error).__name__.lower()
            logger.warning("DhanHQ WebSocket error callback: %s (%s)", error, err_type)
            if any(k in err_str for k in ("401", "403", "unauthorized", "token is expired", "invalid client id", "authentication failed")):
                self.last_error = "🔑 DhanHQ Access Token Expired or Invalid. Please generate a new access token from dhanhq.co and update your .env file or Secrets."
            elif "805" in err_str or "active websocket connections exceeded" in err_str:
                self.last_error = "⚠️ DhanHQ active connection limit exceeded (Only 1 WebSocket allowed per account). Please close duplicate browser tabs or apps."

        def _run_feed_safely():
            try:
                logger.info("Initializing Dhan Scrip Master for background live feed...")
                instruments = self.scrip_master.load_fno_universe(
                    strikes_above_below=self.strikes_above_below,
                    index_strikes_above_below=self.index_strikes_above_below,
                )
                logger.info("Subscribing to %d option & equity contracts...", len(instruments))

                ctx = DhanContext(client_id=self.client_id, access_token=self.access_token)
                self._feed = MarketFeed(
                    dhan_context=ctx,
                    instruments=instruments,
                    version="v2",
                    on_message=_packet_handler,
                    on_close=_on_close,
                    on_error=_on_error,
                )
                logger.info("DhanHQ WebSocket connecting...")
                self._feed.run()
            except Exception as e:
                if not self._running:
                    return
                err_str = str(e).lower()
                err_type = type(e).__name__.lower()
                logger.warning("DhanHQ WebSocket exception: %s (%s)", e, err_type)

                if any(k in err_str for k in ("401", "403", "unauthorized", "807", "token is expired", "invalid client id", "authentication failed")):
                    msg = "🔑 DhanHQ Access Token Expired or Invalid. Please generate a new access token from dhanhq.co and update your .env file or Secrets."
                    logger.warning(msg)
                    self.last_error = msg
                elif "805" in err_str or "active websocket connections exceeded" in err_str:
                    self.last_error = "⚠️ DhanHQ active connection limit exceeded (Only 1 WebSocket allowed per account). Please close duplicate browser tabs or apps."
                else:
                    logger.info("DhanHQ WebSocket disconnected: %s", e)
            finally:
                self._running = False

        thread = threading.Thread(target=_run_feed_safely, daemon=True)
        thread.start()
        logger.info("DhanHQ Live WebSocket background thread started.")

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
