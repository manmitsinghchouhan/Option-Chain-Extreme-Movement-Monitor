import asyncio
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# Ensure root directory is in sys.path for Streamlit Cloud and subdirectory runners
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from app.alerts.manager import AlertManager
from app.data.dhan import DhanMarketDataProvider
from app.data.dummy import DummyMarketDataProvider
from app.data.models import MarketTick, OptionTick, OptionType
from app.data.scrip_master import DhanScripMaster
from app.detection.detector import ExtremeDetector
from app.movement.calculator import calculate_movement
from app.notifications.telegram import TelegramNotifier
from app.state.manager import StateManager
from app.stocks import get_symbols

load_dotenv()
try:
    if hasattr(st, "secrets"):
        for k, v in st.secrets.items():
            if isinstance(v, str) and k not in os.environ:
                os.environ[k] = v
except Exception:
    pass

st.set_page_config(
    page_title="F&O Option Chain Extreme Movement Monitor",
    page_icon="⚡",
    layout="wide",
)

# Custom Styling
st.markdown(
    """
    <style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        background: linear-gradient(90deg, #38bdf8, #818cf8, #c084fc);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0px;
    }
    .live-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 700;
        letter-spacing: 0.5px;
    }
    .live-active {
        background: rgba(34, 197, 94, 0.2);
        color: #4ade80;
        border: 1px solid #22c55e;
        animation: pulse 1.5s infinite;
    }
    .live-paused {
        background: rgba(148, 163, 184, 0.2);
        color: #94a3b8;
        border: 1px solid #64748b;
    }
    @keyframes pulse {
        0% { opacity: 0.7; }
        50% { opacity: 1; }
        100% { opacity: 0.7; }
    }
    .alert-card-up {
        background: rgba(34, 197, 94, 0.08);
        border: 1px solid rgba(34, 197, 94, 0.3);
        border-left: 5px solid #22c55e;
        border-radius: 8px;
        padding: 12px 18px;
        margin-bottom: 12px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
    }
    .alert-card-down {
        background: rgba(239, 68, 68, 0.08);
        border: 1px solid rgba(239, 68, 68, 0.3);
        border-left: 5px solid #ef4444;
        border-radius: 8px;
        padding: 12px 18px;
        margin-bottom: 12px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
    }
    .mover-badge {
        display: inline-block;
        background: rgba(255, 255, 255, 0.07);
        border: 1px solid rgba(255, 255, 255, 0.15);
        border-radius: 6px;
        padding: 4px 10px;
        margin: 3px 6px 3px 0;
        font-size: 0.88rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# -------------------------------------------------------------------
# Session state initialization
# -------------------------------------------------------------------

if "state_manager" not in st.session_state:
    st.session_state.state_manager = StateManager(window_minutes=60)

if "detector" not in st.session_state:
    st.session_state.detector = ExtremeDetector(thresholds=(60.0, 70.0, 80.0), allow_up=False, allow_down=True)

if "alert_manager" not in st.session_state:
    st.session_state.alert_manager = AlertManager()

if "notifier" not in st.session_state:
    st.session_state.notifier = TelegramNotifier()

if "recent_alerts" not in st.session_state:
    st.session_state.recent_alerts = []

if "update_count" not in st.session_state:
    st.session_state.update_count = 0

if "live_ticks_received" not in st.session_state:
    st.session_state.live_ticks_received = 0

if "auto_stream" not in st.session_state:
    st.session_state.auto_stream = False

if "dummy_provider" not in st.session_state:
    st.session_state.dummy_provider = DummyMarketDataProvider(
        update_interval_seconds=0,
        strikes_above_below=4,
        enable_random_spikes=True,
    )

if "dhan_provider" not in st.session_state:
    try:
        st.session_state.dhan_provider = DhanMarketDataProvider(strikes_above_below=6)
    except Exception:
        st.session_state.dhan_provider = None

state_manager: StateManager = st.session_state.state_manager
detector: ExtremeDetector = st.session_state.detector
alert_manager: AlertManager = st.session_state.alert_manager
notifier: TelegramNotifier = st.session_state.notifier
all_symbols = get_symbols()

# -------------------------------------------------------------------
# Sidebar Configuration
# -------------------------------------------------------------------

st.sidebar.title("⚙️ System Settings")

dhan_available = st.session_state.dhan_provider is not None
provider_options = [
    "🟢 DhanHQ Live WebSocket (210 F&O Stocks)",
    "🔘 Simulation Mode (3,780 Dummy Option Contracts)",
] if dhan_available else ["🔘 Simulation Mode (3,780 Dummy Option Contracts)"]

selected_provider_mode = st.sidebar.selectbox(
    "Data Source Mode",
    provider_options,
    index=0 if dhan_available else 0,
)
is_dhan_mode = "DhanHQ" in selected_provider_mode

st.sidebar.markdown("---")
st.sidebar.subheader("🔌 Connection Status")
if is_dhan_mode:
    if st.session_state.dhan_provider and st.session_state.dhan_provider.last_error:
        st.sidebar.error("🔑 DhanHQ Token Expired / Invalid")
        st.sidebar.caption("Please generate a new access token from [dhanhq.co](https://dhanhq.co/) and update your `.env` file or use the box below.")
    else:
        st.sidebar.success("🟢 DhanHQ v2 API: Authenticated")
        st.sidebar.info(f"📊 F&O Universe: {len(all_symbols)} Stocks")
        st.sidebar.caption("⚡ Live WebSocket: wss://api-feed.dhan.co")

    with st.sidebar.expander("🔑 Daily Token Updater"):
        new_token_input = st.text_input("New Dhan Access Token", type="password", key="daily_token_input", help="Paste today's token from dhanhq.co")
        if st.button("Apply Token", width='stretch', key="apply_daily_token"):
            if new_token_input.strip():
                clean_tok = new_token_input.strip()
                os.environ["DHAN_ACCESS_TOKEN"] = clean_tok
                if st.session_state.dhan_provider:
                    st.session_state.dhan_provider.access_token = clean_tok
                    st.session_state.dhan_provider.last_error = None
                    if st.session_state.dhan_provider._running:
                        st.session_state.dhan_provider.stop()
                st.session_state.cached_real_chains = {}
                st.toast("✅ Access token updated! Reconnecting...", icon="🔑")
                st.rerun()
else:
    st.sidebar.info("🔘 Simulation Mode: Active (Brownian Motion)")

if notifier.is_configured():
    st.sidebar.success("📱 Telegram Bot: Connected")
    if st.sidebar.button("🔔 Send Test Telegram Alert", width='stretch'):
        try:
            notifier.send_test_message()
            st.toast("✅ Test Telegram alert sent successfully!", icon="📱")
            st.sidebar.success("✅ Test alert sent to Telegram!")
        except Exception as e:
            st.sidebar.error(f"Failed to send Telegram message: {e}")
else:
    st.sidebar.caption("📱 Telegram: Not configured (add to .env to enable)")

st.sidebar.markdown("---")
refresh_speed = st.sidebar.selectbox(
    "UI Dashboard Refresh Rate",
    options=[1, 2, 3, 5],
    index=1,
    format_func=lambda x: f"⚡ Every {x}s",
    help="Controls how frequently the browser UI repaints with buffered live ticks from the WebSocket thread.",
)

# -------------------------------------------------------------------
# Header & Metrics
# -------------------------------------------------------------------

total_contracts = len(all_symbols) * 18

h_col1, h_col2 = st.columns([3, 1])
with h_col1:
    st.markdown('<h1 class="main-header">⚡ F&O Option Chain Extreme Movement Monitor</h1>', unsafe_allow_html=True)
    mode_text = "DhanHQ Live Real-Time Feed" if is_dhan_mode else "Offline Simulation Mode"
    st.caption(f"Scanner [{mode_text}] — Monitoring 210 F&O Stocks for -60%, -70%, -80% Down Spikes & Crashes")
with h_col2:
    if st.session_state.auto_stream:
        st.markdown('<div style="text-align: right; margin-top: 15px;"><span class="live-badge live-active">● STREAMING LIVE</span></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="text-align: right; margin-top: 15px;"><span class="live-badge live-paused">⏸️ STREAM PAUSED</span></div>', unsafe_allow_html=True)

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("F&O Stocks Tracked", f"{len(all_symbols)} Stocks")
with col2:
    st.metric("Option Contracts", f"{total_contracts:,} CE/PE")
with col3:
    st.metric("Detection Window", "60 min Rolling")
with col4:
    st.metric("Alert Thresholds", "-60%, -70%, -80%")
with col5:
    st.metric("Active Alerts Triggered", f"{len(st.session_state.recent_alerts)}")

st.divider()

if is_dhan_mode and st.session_state.dhan_provider and st.session_state.dhan_provider.last_error:
    st.error(f"🚨 **Authentication Error:** {st.session_state.dhan_provider.last_error}")
    st.info("💡 **Quick Fix:** 1. Generate a new token from [DhanHQ Portal](https://dhanhq.co/) ➔ 2. Update `DHAN_ACCESS_TOKEN` in `.env` ➔ 3. Click '🔄 Refresh Data' or toggle Live Streaming.")

# -------------------------------------------------------------------
# Streaming Controls
# -------------------------------------------------------------------

ctrl1, ctrl2, ctrl3, ctrl4 = st.columns([2, 2, 2, 1.5])

with ctrl1:
    auto_stream_toggle = st.toggle("🟢 Live Auto-Streaming", value=st.session_state.auto_stream)
    if auto_stream_toggle != st.session_state.auto_stream:
        st.session_state.auto_stream = auto_stream_toggle
        if auto_stream_toggle and is_dhan_mode and st.session_state.dhan_provider:
            load_dotenv(override=True)
            st.session_state.dhan_provider.access_token = os.getenv("DHAN_ACCESS_TOKEN")
            st.session_state.dhan_provider.last_error = None
        st.rerun()

with ctrl2:
    run_single_batch = st.button("▶ Step One Batch", width='stretch', disabled=st.session_state.auto_stream or is_dhan_mode)

with ctrl3:
    inject_spike = st.button("💥 Inject Down Spike (-75%)", width='stretch', disabled=is_dhan_mode)

with ctrl4:
    if st.button("🗑 Clear State", width='stretch'):
        state_manager.clear()
        alert_manager.clear()
        st.session_state.recent_alerts = []
        st.session_state.update_count = 0
        st.session_state.live_ticks_received = 0
        st.success("State cleared.")
        st.rerun()

if is_dhan_mode and not st.session_state.auto_stream:
    st.info("💡 **DhanHQ Mode is selected.** Turn ON **'🟢 Live Auto-Streaming'** to start receiving real-time ticks from the DhanHQ WebSocket.")

# -------------------------------------------------------------------
# Background Dhan WebSocket Feed Launcher
# -------------------------------------------------------------------

if is_dhan_mode and st.session_state.auto_stream:
    if st.session_state.dhan_provider:
        if st.session_state.dhan_provider.last_error:
            # Auto-reset streaming and immediately rerun to render toggle as OFF in UI
            st.session_state.auto_stream = False
            st.session_state.dhan_provider.stop()
            st.rerun()
        elif not st.session_state.dhan_provider._running:
            st.session_state.dhan_provider.start_background_feed(state_manager.update)
elif not st.session_state.auto_stream or not is_dhan_mode:
    if st.session_state.dhan_provider and st.session_state.dhan_provider._running:
        st.session_state.dhan_provider.stop()

# -------------------------------------------------------------------
# Execution Engine (Strict separation of Dhan vs Dummy)
# -------------------------------------------------------------------

should_process = st.session_state.auto_stream or run_single_batch or inject_spike

if inject_spike and not is_dhan_mode:
    spiked_key = st.session_state.dummy_provider.inject_extreme_spike(percentage_change=-75.0)
    st.toast(f"⚡ Injected -75% crash into contract: {spiked_key}!", icon="📉")

if should_process:
    ticks: list[OptionTick] = []

    if is_dhan_mode and st.session_state.dhan_provider:
        # Drain all real live ticks that arrived in DhanMarketDataProvider live_queue
        while not st.session_state.dhan_provider.live_queue.empty():
            try:
                tick = st.session_state.dhan_provider.live_queue.get_nowait()
                ticks.append(tick)
            except Exception:
                break
        
        st.session_state.live_ticks_received += len(ticks)
        st.session_state.update_count += 1
    else:
        # In Simulation Mode, generate simulated ticks
        ticks = st.session_state.dummy_provider.generate_option_ticks()
        st.session_state.update_count += 1

    new_alerts = []

    for tick in ticks:
        state_manager.update(tick)

        if not isinstance(tick, OptionTick):
            continue

        history = state_manager.get_history(tick.instrument_key)

        if len(history) < 2:
            continue

        movement = calculate_movement(history)
        if movement is None:
            continue

        # Automatically reset active threshold flags if price retreats below threshold
        alert_manager.sync_resets(
            instrument_key=tick.instrument_key,
            percentage_change=movement.percentage_change,
            thresholds=detector.thresholds,
        )

        events = detector.detect(
            symbol=tick.symbol,
            movement=movement,
            strike_price=tick.strike_price,
            option_type=tick.option_type,
            expiry=tick.expiry,
            instrument_key=tick.instrument_key,
            underlying_price=tick.underlying_price,
        )

        for event in events:
            if alert_manager.process(event):
                new_alerts.append(event)
                if notifier.is_configured():
                    try:
                        notifier.send(event)
                    except Exception:
                        pass

    if new_alerts:
        st.session_state.recent_alerts = new_alerts + st.session_state.recent_alerts
        st.session_state.recent_alerts = st.session_state.recent_alerts[:100]
        st.toast(f"🚨 {len(new_alerts)} New Extreme Movement Alert(s) Detected!", icon="⚡")

if is_dhan_mode and st.session_state.auto_stream and st.session_state.live_ticks_received == 0:
    st.warning("🌙 **Market Currently Closed:** Connected to DhanHQ Live WebSocket, but NSE is currently closed (Trading hours: 9:15 AM - 3:30 PM IST). 0 live trade ticks received after hours.")

# -------------------------------------------------------------------
# Top Market Movers Bar (Real-Time Scanner)
# -------------------------------------------------------------------

movers = []
for symbol in all_symbols:
    for key in state_manager.get_instruments_for_symbol(symbol):
        hist = state_manager.get_history(key)
        if len(hist) >= 2:
            pct = ((hist[-1].price - hist[0].price) / hist[0].price) * 100
            if abs(pct) >= 5.0:
                parts = key.split("_")
                readable = f"{parts[0]} {parts[2]} {parts[3]}"
                movers.append((readable, pct, hist[-1].price))

if movers:
    movers.sort(key=lambda x: abs(x[1]), reverse=True)
    st.markdown("**🔥 Top Moving Option Contracts (Rolling Window):**")
    mover_html = ""
    for name, pct, ltp in movers[:6]:
        color = "#4ade80" if pct > 0 else "#f87171"
        icon = "▲" if pct > 0 else "▼"
        mover_html += f'<span class="mover-badge"><strong>{name}</strong>: <span style="color:{color}; font-weight:700;">{icon} {pct:+.1f}%</span> (₹{ltp:.2f})</span>'
    st.markdown(mover_html, unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

# -------------------------------------------------------------------
# Live Extreme Alerts Feed
# -------------------------------------------------------------------

st.subheader("🚨 Live Extreme Option Alerts")

if st.session_state.recent_alerts:
    f_col1, f_col2 = st.columns([1, 1])
    with f_col1:
        symbols_with_alerts = sorted(list({e.symbol for e in st.session_state.recent_alerts}))
        selected_alert_symbol = st.selectbox("Filter alerts by Stock", ["ALL"] + symbols_with_alerts)
    with f_col2:
        selected_dir = st.selectbox("Filter by Direction", ["ALL", "UP (+)", "DOWN (-)"])

    filtered_alerts = st.session_state.recent_alerts
    if selected_alert_symbol != "ALL":
        filtered_alerts = [e for e in filtered_alerts if e.symbol == selected_alert_symbol]
    if selected_dir == "UP (+)":
        filtered_alerts = [e for e in filtered_alerts if e.direction.value == "UP"]
    elif selected_dir == "DOWN (-)":
        filtered_alerts = [e for e in filtered_alerts if e.direction.value == "DOWN"]

    for event in filtered_alerts[:12]:
        is_up = event.direction.value == "UP"
        card_class = "alert-card-up" if is_up else "alert-card-down"
        badge_color = "#22c55e" if is_up else "#ef4444"
        dir_icon = "🟢 UP" if is_up else "🔴 DOWN"
        spot_val = event.underlying_price if event.underlying_price > 0 else (state_manager.get_underlying_price(event.symbol) or 0.0)
        spot_html = f" | 📈 Spot: <strong>₹{spot_val:.2f}</strong>" if spot_val > 0 else ""
        expiry_html = f" | 🏷️ Expiry: <strong>{event.expiry}</strong>" if event.expiry else ""

        card_html = (
            f'<div class="{card_class}">'
            f'<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">'
            f'<div>'
            f'<strong style="font-size: 1.15rem; color: #f8fafc;">{event.display_title}</strong>'
            f'<span style="background: {badge_color}; color: #020617; font-weight: 700; padding: 2px 8px; border-radius: 4px; margin-left: 8px;">'
            f'{event.percentage_change:+.2f}% ({dir_icon})'
            f'</span>'
            f'<span style="color: #94a3b8; margin-left: 8px; font-weight: 500;">Threshold: {event.threshold:.0f}%</span>'
            f'</div>'
            f'<div style="font-size: 0.9rem; color: #cbd5e1;">'
            f'⏱️ {event.current_timestamp.strftime("%H:%M:%S")} ({event.duration_seconds/60:.1f}m window)'
            f'</div>'
            f'</div>'
            f'<div style="margin-top: 8px; font-size: 0.95rem; color: #cbd5e1;">'
            f'💰 Premium: <strong>₹{event.start_price:.2f}</strong> ➔ <strong>₹{event.current_price:.2f}</strong>'
            f'{spot_html}'
            f'{expiry_html}'
            f'</div>'
            f'</div>'
        )
        st.markdown(card_html, unsafe_allow_html=True)
else:
    if is_dhan_mode:
        st.info("Waiting for live extreme option movements from DhanHQ WebSocket. (0 alerts triggered).")
    else:
        st.info("No extreme option premium jumps detected yet. Turn ON **'🟢 Live Auto-Streaming'** to scan simulation.")

st.divider()

# -------------------------------------------------------------------
# Option Chain Matrix Viewer (Zero-Lag Cached Snapshot + Live WebSocket Overlay)
# -------------------------------------------------------------------

st.subheader("📊 Live Option Chain Matrix")

if "cached_real_chains" not in st.session_state:
    st.session_state.cached_real_chains = {}

stock_col, ref_col = st.columns([4, 1.2])

with stock_col:
    selected_stock = st.selectbox(
        "Select Stock to View Option Chain",
        all_symbols,
        index=all_symbols.index("RELIANCE") if "RELIANCE" in all_symbols else 0,
        key="selected_stock_matrix",
    )

with ref_col:
    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
    refresh_snapshot = st.button("🔄 Refresh Data", width='stretch', help="Fetch fresh snapshot from DhanHQ REST API")

chain_rows = []

if is_dhan_mode and st.session_state.dhan_provider:
    # Only make network REST call once per stock selection or on manual refresh
    if selected_stock not in st.session_state.cached_real_chains or refresh_snapshot:
        if refresh_snapshot:
            load_dotenv(override=True)
            st.session_state.dhan_provider.access_token = os.getenv("DHAN_ACCESS_TOKEN")
            st.session_state.dhan_provider.last_error = None
        with st.spinner(f"Loading DhanHQ real option chain snapshot for {selected_stock}..."):
            real_chain = st.session_state.dhan_provider.fetch_real_option_chain(selected_stock)
            if real_chain:
                st.session_state.cached_real_chains[selected_stock] = real_chain
                if real_chain.get("spot_price", 0) > 0:
                    state_manager.update(MarketTick(
                        symbol=selected_stock,
                        price=real_chain["spot_price"],
                        timestamp=datetime.now(),
                        volume=0,
                    ))
                for s in real_chain.get("strikes", []):
                    if s.get("ce_ltp", 0) > 0:
                        ce_t = OptionTick(
                            symbol=selected_stock,
                            strike_price=s["strike"],
                            option_type=OptionType.CE,
                            expiry=real_chain["expiry"],
                            premium=s["ce_ltp"],
                            timestamp=datetime.now(),
                            volume=s.get("ce_volume", 0),
                            open_interest=s.get("ce_oi", 0),
                            underlying_price=real_chain["spot_price"],
                        )
                        if not state_manager.get_latest(ce_t.instrument_key):
                            state_manager.update(ce_t)
                    if s.get("pe_ltp", 0) > 0:
                        pe_t = OptionTick(
                            symbol=selected_stock,
                            strike_price=s["strike"],
                            option_type=OptionType.PE,
                            expiry=real_chain["expiry"],
                            premium=s["pe_ltp"],
                            timestamp=datetime.now(),
                            volume=s.get("pe_volume", 0),
                            open_interest=s.get("pe_oi", 0),
                            underlying_price=real_chain["spot_price"],
                        )
                        if not state_manager.get_latest(pe_t.instrument_key):
                            state_manager.update(pe_t)

    cached_chain = st.session_state.cached_real_chains.get(selected_stock)

    if cached_chain and cached_chain.get("strikes"):
        spot_price = state_manager.get_underlying_price(selected_stock) or cached_chain["spot_price"]
        spot_hist = state_manager.get_history(selected_stock)
        spot_icon = "🟢 " if len(spot_hist) >= 2 else ""
        expiry_date = cached_chain["expiry"]
        st.markdown(
            f"**Underlying Spot:** `{selected_stock}` @ **{spot_icon}₹{spot_price:.2f}** | "
            f"**Expiry:** `{expiry_date}` | **Source:** `DhanHQ Real Market Data` | "
            f"**Live Ticks Ingested:** `{st.session_state.live_ticks_received:,}`"
        )

        for item in cached_chain["strikes"]:
            strike = item["strike"]
            ce_key = f"{selected_stock}_{expiry_date}_{strike:.0f}_CE"
            pe_key = f"{selected_stock}_{expiry_date}_{strike:.0f}_PE"

            # Check if live WebSocket pushed updated ticks into state_manager
            ce_live = state_manager.get_latest(ce_key)
            pe_live = state_manager.get_latest(pe_key)

            ce_ltp = ce_live.price if ce_live else item["ce_ltp"]
            ce_vol = ce_live.volume if (ce_live and ce_live.volume > 0) else item["ce_volume"]
            ce_oi = ce_live.open_interest if (ce_live and ce_live.open_interest > 0) else item["ce_oi"]
            ce_prev = item["ce_prev_close"]

            pe_ltp = pe_live.price if pe_live else item["pe_ltp"]
            pe_vol = pe_live.volume if (pe_live and pe_live.volume > 0) else item["pe_volume"]
            pe_oi = pe_live.open_interest if (pe_live and pe_live.open_interest > 0) else item["pe_oi"]
            pe_prev = item["pe_prev_close"]

            ce_chg = f"{((ce_ltp - ce_prev) / ce_prev) * 100:+.1f}%" if ce_prev > 0 and ce_ltp > 0 else "0.0%"
            pe_chg = f"{((pe_ltp - pe_prev) / pe_prev) * 100:+.1f}%" if pe_prev > 0 and pe_ltp > 0 else "0.0%"

            ce_hist = state_manager.get_history(ce_key)
            pe_hist = state_manager.get_history(pe_key)
            ce_icon = "🟢 " if len(ce_hist) >= 2 else ""
            pe_icon = "🟢 " if len(pe_hist) >= 2 else ""

            chain_rows.append({
                "CE OI": f"{ce_oi:,}",
                "CE Volume": f"{ce_vol:,}",
                "CE Chg %": ce_chg,
                "CALL (CE) LTP (₹)": f"{ce_icon}₹{ce_ltp:.2f}",
                "🎯 STRIKE (₹)": f"₹{strike:.0f}",
                "PUT (PE) LTP (₹)": f"{pe_icon}₹{pe_ltp:.2f}",
                "PE Chg %": pe_chg,
                "PE Volume": f"{pe_vol:,}",
                "PE OI": f"{pe_oi:,}",
            })
    else:
        if st.session_state.dhan_provider and st.session_state.dhan_provider.last_error:
            st.error(f"{st.session_state.dhan_provider.last_error}")
        else:
            st.warning(f"Could not load option chain for {selected_stock} from Dhan API. Click '🔄 Refresh Data' to retry.")

else:
    # Simulation Mode Table
    dummy_p: DummyMarketDataProvider = st.session_state.dummy_provider
    underlying_price = state_manager.get_underlying_price(selected_stock) or dummy_p.spot_prices.get(selected_stock, 0.0)
    strikes = dummy_p.get_strikes_for_symbol(selected_stock)

    st.markdown(f"**Underlying Spot:** `{selected_stock}` @ **₹{underlying_price:.2f}** | **Expiry:** `{dummy_p.expiry_date}` | **Source:** `Simulation Mode` | **Simulated Ticks:** `{st.session_state.update_count * 3780:,}`")

    for strike in strikes:
        ce_key = f"{selected_stock}_{dummy_p.expiry_date}_{strike:.0f}_CE"
        pe_key = f"{selected_stock}_{dummy_p.expiry_date}_{strike:.0f}_PE"

        ce_obs = state_manager.get_latest(ce_key)
        pe_obs = state_manager.get_latest(pe_key)

        ce_price = ce_obs.price if ce_obs else dummy_p.premiums.get(ce_key, 0.0)
        ce_vol = ce_obs.volume if ce_obs else dummy_p.volumes.get(ce_key, 0)
        ce_oi = ce_obs.open_interest if ce_obs else dummy_p.open_interests.get(ce_key, 0)

        pe_price = pe_obs.price if pe_obs else dummy_p.premiums.get(pe_key, 0.0)
        pe_vol = pe_obs.volume if pe_obs else dummy_p.volumes.get(pe_key, 0)
        pe_oi = pe_obs.open_interest if pe_obs else dummy_p.open_interests.get(pe_key, 0)

        ce_hist = state_manager.get_history(ce_key)
        pe_hist = state_manager.get_history(pe_key)
        ce_chg = f"{((ce_price - ce_hist[0].price)/ce_hist[0].price)*100:+.1f}%" if len(ce_hist) >= 2 else "0.0%"
        pe_chg = f"{((pe_price - pe_hist[0].price)/pe_hist[0].price)*100:+.1f}%" if len(pe_hist) >= 2 else "0.0%"

        chain_rows.append({
            "CE OI": f"{ce_oi:,}",
            "CE Volume": f"{ce_vol:,}",
            "CE Chg %": ce_chg,
            "CALL (CE) LTP (₹)": f"₹{ce_price:.2f}",
            "🎯 STRIKE (₹)": f"₹{strike:.0f}",
            "PUT (PE) LTP (₹)": f"₹{pe_price:.2f}",
            "PE Chg %": pe_chg,
            "PE Volume": f"{pe_vol:,}",
            "PE OI": f"{pe_oi:,}",
        })

if chain_rows:
    df_chain = pd.DataFrame(chain_rows)
    st.dataframe(df_chain, width='stretch', hide_index=True)

# -------------------------------------------------------------------
# Auto-stream loop
# -------------------------------------------------------------------

if st.session_state.auto_stream:
    if is_dhan_mode and st.session_state.dhan_provider and st.session_state.dhan_provider.last_error:
        st.session_state.auto_stream = False
        st.rerun()
    else:
        time.sleep(refresh_speed)
        st.rerun()
