import asyncio
import os
import queue
import sys
import threading
import time
from datetime import datetime, timezone, timedelta, time as dt_time
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_now() -> datetime:
    return datetime.now(IST)

def check_market_session_ist() -> tuple[bool, str]:
    """Check if Indian NSE Stock Exchange is actively open."""
    now_ist = get_ist_now()
    weekday = now_ist.weekday()  # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday
    current_time = now_ist.time()
    
    if weekday >= 5:
        day_name = "Saturday" if weekday == 5 else "Sunday"
        return False, f"Weekend ({day_name}) — NSE is Closed"
    
    open_time = dt_time(9, 15)
    close_time = dt_time(15, 30)
    
    if current_time < open_time:
        return False, f"Pre-Market (Opens at 9:15 AM IST, Current: {now_ist.strftime('%I:%M %p')} IST)"
    elif current_time > close_time:
        return False, f"Post-Market (Closed at 3:30 PM IST, Current: {now_ist.strftime('%I:%M %p')} IST)"
    
    return True, f"Live Trading Session ({now_ist.strftime('%I:%M %p')} IST)"

# Ensure root directory is in sys.path for Streamlit Cloud and subdirectory runners
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

try:
    from app.data.models import MarketTick, OptionTick, OptionType, PinnedTrade
except ImportError:
    from app.data.models import MarketTick, OptionTick, OptionType

try:
    from app.detection.detector import ExtremeEvent, PENNY_PREMIUM_THRESHOLD
except ImportError:
    from app.detection.detector import ExtremeEvent
    PENNY_PREMIUM_THRESHOLD = 1.00

from app.engine import MonitorEngine
from app.stocks import get_symbols

def sync_secrets_to_env() -> list[str]:
    """Sync all Streamlit cloud secrets and .env keys into os.environ."""
    load_dotenv(override=True)
    detected_keys = []
    try:
        if hasattr(st, "secrets"):
            for k in list(st.secrets.keys()):
                v = st.secrets[k]
                if isinstance(v, dict) or "secrets" in str(type(v)).lower():
                    for sub_k, sub_v in v.items():
                        sub_name = f"{k}_{sub_k}".upper()
                        val = str(sub_v).strip().strip('"').strip("'")
                        os.environ[sub_name] = val
                        os.environ[sub_k.upper()] = val
                        detected_keys.append(f"{k}.{sub_k}")
                else:
                    val = str(v).strip().strip('"').strip("'")
                    os.environ[k.upper()] = val
                    os.environ[k] = val
                    detected_keys.append(k)
    except Exception:
        pass
    return detected_keys


detected_secret_keys = sync_secrets_to_env()

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
    .alert-card-60 {
        background: rgba(16, 185, 129, 0.08);
        border: 1px solid rgba(16, 185, 129, 0.3);
        border-left: 6px solid #10b981;
        border-radius: 8px;
        padding: 12px 18px;
        margin-bottom: 12px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
    }
    .alert-card-70 {
        background: rgba(245, 158, 11, 0.09);
        border: 1px solid rgba(245, 158, 11, 0.35);
        border-left: 6px solid #f59e0b;
        border-radius: 8px;
        padding: 12px 18px;
        margin-bottom: 12px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
    }
    .alert-card-80 {
        background: rgba(239, 68, 68, 0.12);
        border: 1px solid rgba(239, 68, 68, 0.4);
        border-left: 6px solid #ef4444;
        border-radius: 8px;
        padding: 12px 18px;
        margin-bottom: 12px;
        box-shadow: 0 4px 16px rgba(239, 68, 68, 0.15);
    }
    .alert-card-up {
        background: rgba(56, 189, 248, 0.08);
        border: 1px solid rgba(56, 189, 248, 0.3);
        border-left: 6px solid #38bdf8;
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


engine = MonitorEngine.get_instance()
state_manager = engine.state_manager
detector = engine.detector
alert_manager = engine.alert_manager
notifier = engine.notifier
all_symbols = get_symbols()

# -------------------------------------------------------------------
# Sidebar Configuration
# -------------------------------------------------------------------

st.sidebar.title("⚙️ System Settings")

provider_options = [
    "🟢 DhanHQ Live WebSocket (210 F&O Stocks)",
    "🔘 Simulation Mode (3,780 Dummy Option Contracts)",
]

selected_provider_mode = st.sidebar.selectbox(
    "Data Source Mode",
    provider_options,
    index=0,
)
is_dhan_mode = "DhanHQ" in selected_provider_mode

st.sidebar.markdown("---")
st.sidebar.subheader("🔌 Connection Status")
if is_dhan_mode:
    if engine.dhan_provider and engine.dhan_provider.last_error:
        st.sidebar.error(engine.dhan_provider.last_error)
        st.sidebar.caption("Provide your Dhan Client ID & Access Token below or in Streamlit Cloud Secrets.")
    else:
        st.sidebar.success("🟢 DhanHQ v2 API: Authenticated")
        st.sidebar.info(f"📊 F&O Universe: {len(all_symbols)} Stocks")
        st.sidebar.caption("⚡ Live WebSocket: wss://api-feed.dhan.co")

    with st.sidebar.expander("🔑 Daily Access Token Updater"):
        new_token_input = st.text_input("Dhan Access Token", type="password", key="daily_token_input", help="Paste today's access token from dhanhq.co")
        cid_needed = not bool(os.getenv("DHAN_CLIENT_ID"))
        new_cid_input = ""
        if cid_needed:
            new_cid_input = st.text_input("Dhan Client ID", type="password", key="daily_client_id_input", help="Enter Dhan Client ID (only needed once)")
        
        if st.button("Apply & Connect", width='stretch', key="apply_daily_token"):
            tok = new_token_input.strip()
            cid = new_cid_input.strip() if cid_needed else os.getenv("DHAN_CLIENT_ID", "")
            if tok and cid:
                engine.update_dhan_credentials(cid, tok)
                st.toast("✅ DhanHQ credentials updated! Reconnecting...", icon="🔑")
                st.rerun()
            else:
                st.warning("Please enter your Access Token.")
else:
    st.sidebar.info("🔘 Simulation Mode: Active (Brownian Motion)")

# Sidebar Telegram Status
st.sidebar.markdown("---")
st.sidebar.subheader("📱 Telegram Alerts")

# 1. Primary Core Bot (>= ₹1.00)
is_core_configured = hasattr(notifier, "is_configured") and notifier.is_configured()
if is_core_configured:
    st.sidebar.success("🟢 Primary Bot (Core ≥₹1.00): Connected")
    if st.sidebar.button("🔔 Test Core Bot Alert", width='stretch'):
        try:
            if hasattr(notifier, "send_test_message"):
                notifier.send_test_message()
            st.toast("✅ Test alert sent to Primary Core channel!", icon="📱")
            st.sidebar.success("✅ Primary bot test alert sent!")
        except Exception as e:
            st.sidebar.error(f"Core Bot error: {e}")
else:
    st.sidebar.caption("📱 Core Bot: Not configured (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)")

# 2. Expiry Penny Bot (< ₹1.00)
is_penny_configured = hasattr(notifier, "is_penny_configured") and notifier.is_penny_configured()
if is_penny_configured:
    st.sidebar.success("📉 Expiry Bot (Penny <₹1.00): Connected")
    if st.sidebar.button("📉 Test Penny Bot Alert", width='stretch'):
        try:
            if hasattr(notifier, "send_test_penny_message"):
                notifier.send_test_penny_message()
            st.toast("✅ Test alert sent to Penny Decay channel!", icon="📉")
            st.sidebar.success("✅ Penny bot test alert sent!")
        except Exception as e:
            st.sidebar.error(f"Penny Bot error: {e}")
else:
    st.sidebar.caption("📉 Penny Bot: Inactive (`TELEGRAM_PENNY_CHAT_ID` not set)")

if not is_core_configured or not is_penny_configured:
    if st.sidebar.button("🔄 Reload Cloud Secrets", width='stretch', help="Click to reload secrets from Streamlit settings without rebooting"):
        sync_secrets_to_env()
        st.toast("🔄 Cloud secrets reloaded!", icon="🔑")
        st.rerun()

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

is_market_open, market_session_status = check_market_session_ist()

total_contracts = len(all_symbols) * 18
core_alerts_list = engine.core_alerts
penny_alerts_list = engine.penny_alerts

h_col1, h_col2 = st.columns([3, 1])
with h_col1:
    st.markdown('<h1 class="main-header">⚡ F&O Option Chain Extreme Movement Monitor</h1>', unsafe_allow_html=True)
    mode_text = "DhanHQ Live Real-Time Feed" if is_dhan_mode else "Offline Simulation Mode"
    st.caption(f"Scanner [{mode_text}] — Monitoring 210 F&O Stocks for -60%, -70%, -80% Down Spikes & Crashes")
with h_col2:
    if is_dhan_mode:
        if not is_market_open:
            st.markdown('<div style="text-align: right; margin-top: 15px;"><span class="live-badge live-paused">🌙 MARKET CLOSED</span></div>', unsafe_allow_html=True)
        elif engine.is_streaming_active:
            st.markdown('<div style="text-align: right; margin-top: 15px;"><span class="live-badge live-active">● STREAMING LIVE</span></div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="text-align: right; margin-top: 15px;"><span class="live-badge live-paused">⏸️ STREAM PAUSED</span></div>', unsafe_allow_html=True)
    else:
        if engine.is_streaming_active:
            st.markdown('<div style="text-align: right; margin-top: 15px;"><span class="live-badge live-active">● SIMULATION ACTIVE</span></div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="text-align: right; margin-top: 15px;"><span class="live-badge live-paused">⏸️ STREAM PAUSED</span></div>', unsafe_allow_html=True)

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("F&O Stocks Tracked", f"{len(all_symbols)} Stocks")
with col2:
    st.metric("Option Contracts", f"{total_contracts:,} CE/PE")
with col3:
    st.metric("Alert Thresholds", "-60%, -70%, -80%")
with col4:
    st.metric("🚨 Core Crashes (≥₹1.00)", f"{len(core_alerts_list)}")
with col5:
    st.metric("📉 Penny Decay (<₹1.00)", f"{len(penny_alerts_list)}")

st.divider()

if is_dhan_mode:
    if engine.dhan_provider and engine.dhan_provider.last_error:
        st.error(f"🚨 **Authentication Error:** {engine.dhan_provider.last_error}")
        st.info("💡 **Quick Fix:** 1. Generate a new token from [DhanHQ Portal](https://dhanhq.co/) ➔ 2. Paste in sidebar 'Daily Access Token Updater' or update Secrets.")
    elif not is_market_open:
        st.warning(
            f"🌙 **NSE Market is Closed ({market_session_status})**\n\n"
            f"• Trading hours: **Monday – Friday, 9:15 AM – 3:30 PM IST**.\n"
            f"• Displaying the **last recorded closing market snapshot** and historical price data.\n"
            f"• Live WebSocket tick stream and extreme alerts will resume automatically at **9:15 AM IST**."
        )

# -------------------------------------------------------------------
# Streaming Controls
# -------------------------------------------------------------------

ctrl1, ctrl2, ctrl3, ctrl4 = st.columns([2, 2, 2, 1.5])

with ctrl1:
    auto_stream_toggle = st.toggle("🟢 Live Auto-Streaming", value=engine.is_streaming_active)
    if auto_stream_toggle != engine.is_streaming_active:
        if auto_stream_toggle:
            if is_dhan_mode:
                load_dotenv(override=True)
                engine.dhan_provider.access_token = os.getenv("DHAN_ACCESS_TOKEN", engine.dhan_provider.access_token)
                engine.start_dhan_feed()
            else:
                engine.is_streaming_active = True
        else:
            engine.stop_dhan_feed()
        st.rerun()

with ctrl2:
    run_single_batch = st.button("▶ Step One Batch", width='stretch', disabled=engine.is_streaming_active or is_dhan_mode)

with ctrl3:
    inject_spike = st.button("💥 Inject Down Spike (-75%)", width='stretch', disabled=is_dhan_mode)

with ctrl4:
    if st.button("🗑 Clear State", width='stretch'):
        engine.clear_state()
        st.success("Server state cleared.")
        st.rerun()

if is_dhan_mode and not engine.is_streaming_active:
    st.info("💡 **DhanHQ Mode is selected.** Turn ON **'🟢 Live Auto-Streaming'** to start receiving real-time ticks from the DhanHQ WebSocket.")

# -------------------------------------------------------------------
# Execution Engine (Simulation & Background Sync)
# -------------------------------------------------------------------

if inject_spike and not is_dhan_mode:
    spiked_key = engine.inject_simulation_spike(percentage_change=-75.0)
    st.toast(f"⚡ Injected -75% crash into contract: {spiked_key}!", icon="📉")

if run_single_batch and not is_dhan_mode:
    new_sim_alerts = engine.step_simulation()
    if new_sim_alerts:
        st.toast(f"🚨 {len(new_sim_alerts)} New Extreme Movement Alert(s) Detected!", icon="⚡")

if engine.is_streaming_active and not is_dhan_mode:
    new_sim_alerts = engine.step_simulation()
    if new_sim_alerts:
        st.toast(f"🚨 {len(new_sim_alerts)} New Extreme Movement Alert(s) Detected!", icon="⚡")

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
# Pinned Trades & Active Watchlist (Always-Visible Top Priority Station)
# -------------------------------------------------------------------

pinned_list = getattr(engine, "pinned_trades_list", [])

st.subheader(f"📌 Pinned Trades & Active Watchlist ({len(pinned_list)})")

if pinned_list:
    p_header_col1, p_header_col2 = st.columns([4, 1.2])
    with p_header_col1:
        st.caption("⚡ **Live P&L Tracking:** Return updates continuously via real-time WebSocket ticks.")
    with p_header_col2:
        if st.button("🗑️ Clear All Pinned", width='stretch', key="clear_all_pinned_btn"):
            if hasattr(engine, "clear_pinned_trades"):
                engine.clear_pinned_trades()
            st.toast("🗑️ Cleared all pinned trades!", icon="📌")
            st.rerun()

    for idx, item in enumerate(pinned_list):
        # Query latest live price from state_manager
        obs = state_manager.get_latest(item.instrument_key)
        if obs and obs.price > 0:
            current_live_price = obs.price
        elif not is_dhan_mode:
            current_live_price = engine.dummy_provider.premiums.get(item.instrument_key, item.pinned_price)
        else:
            current_live_price = item.pinned_price

        # Calculate live return since pinned
        if item.pinned_price > 0:
            live_return_pct = ((current_live_price - item.pinned_price) / item.pinned_price) * 100
        else:
            live_return_pct = 0.0

        if live_return_pct > 0:
            return_color = "#4ade80"
            return_icon = "▲"
            return_bg = "rgba(34, 197, 94, 0.15)"
            return_border = "#22c55e"
        elif live_return_pct < 0:
            return_color = "#f87171"
            return_icon = "▼"
            return_bg = "rgba(239, 68, 68, 0.15)"
            return_border = "#ef4444"
        else:
            return_color = "#94a3b8"
            return_icon = "●"
            return_bg = "rgba(148, 163, 184, 0.15)"
            return_border = "#64748b"

        p_card_col, p_action_col = st.columns([5.2, 0.9])
        with p_card_col:
            underlying_spot = state_manager.get_underlying_price(item.symbol) or item.underlying_price
            spot_str = f" | 📈 Spot: <strong>₹{underlying_spot:.2f}</strong>" if underlying_spot > 0 else ""
            expiry_str = f" | 🏷️ Expiry: <strong>{item.expiry}</strong>" if item.expiry else ""

            pinned_card_html = (
                f'<div style="background: rgba(15, 23, 42, 0.75); border: 1px solid rgba(56, 189, 248, 0.35); border-left: 6px solid #38bdf8; border-radius: 8px; padding: 12px 18px; margin-bottom: 8px;">'
                f'<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">'
                f'<div>'
                f'<strong style="font-size: 1.15rem; color: #f8fafc;">📌 {item.display_title}</strong>'
                f'<span style="background: {return_bg}; color: {return_color}; border: 1px solid {return_border}; font-weight: 700; padding: 3px 10px; border-radius: 4px; margin-left: 10px; font-size: 0.9rem;">'
                f'{return_icon} {live_return_pct:+.2f}% Live Return'
                f'</span>'
                f'</div>'
                f'<div style="font-size: 0.85rem; color: #94a3b8;">'
                f'Pinned at: {item.pinned_timestamp.strftime("%H:%M:%S")}'
                f'</div>'
                f'</div>'
                f'<div style="margin-top: 6px; font-size: 0.95rem; color: #cbd5e1;">'
                f'💰 Pinned Entry: <strong>₹{item.pinned_price:.2f}</strong> ➔ Live Now: <strong style="color: {return_color};">₹{current_live_price:.2f}</strong>'
                f'{spot_str}'
                f'{expiry_str}'
                f'</div>'
                f'</div>'
            )
            st.markdown(pinned_card_html, unsafe_allow_html=True)
        with p_action_col:
            st.markdown("<div style='margin-top: 8px;'></div>", unsafe_allow_html=True)
            if st.button("❌ Remove", key=f"unpin_btn_{item.instrument_key}_{idx}", width='stretch', help="Remove from pinned watchlist"):
                if hasattr(engine, "unpin_trade"):
                    engine.unpin_trade(item.instrument_key)
                st.toast(f"Removed {item.display_title} from pinned trades.", icon="❌")
                st.rerun()
else:
    st.info("💡 **No pinned trades yet.** Click **'📌 Pin'** on any alert below to lock it here for live return tracking.")

st.divider()

# -------------------------------------------------------------------
# Live Extreme Alerts Feed (Two-Tier Architecture)
# -------------------------------------------------------------------

st.subheader("🚨 Live Extreme Option Alerts")

tab_core, tab_penny = st.tabs([
    f"🚨 Core Option Crashes (≥ ₹1.00) — [{len(core_alerts_list)}]",
    f"📉 Expiry Penny Decay (< ₹1.00) — [{len(penny_alerts_list)}]",
])

def render_alert_cards(alert_list: list[ExtremeEvent], tab_key: str, empty_msg: str):
    if not alert_list:
        st.info(empty_msg)
        return

    f_col1, f_col2 = st.columns([1, 1])
    with f_col1:
        symbols_in_tab = sorted(list({e.symbol for e in alert_list}))
        selected_stock_filter = st.selectbox("Filter by Stock", ["ALL"] + symbols_in_tab, key=f"filter_stock_{tab_key}")
    with f_col2:
        selected_dir_filter = st.selectbox("Filter by Direction", ["ALL", "UP (+)", "DOWN (-)"], key=f"filter_dir_{tab_key}")

    filtered = alert_list
    if selected_stock_filter != "ALL":
        filtered = [e for e in filtered if e.symbol == selected_stock_filter]
    if selected_dir_filter == "UP (+)":
        filtered = [e for e in filtered if e.direction.value == "UP"]
    elif selected_dir_filter == "DOWN (-)":
        filtered = [e for e in filtered if e.direction.value == "DOWN"]

    for idx, event in enumerate(filtered[:15]):
        is_up = event.direction.value == "UP"
        if is_up:
            card_class = "alert-card-up"
            badge_color = "#38bdf8"
            badge_text_color = "#020617"
            dir_icon = "🟢 UP"
        else:
            # Downward crash color differentiation based on threshold severity
            if event.threshold >= 80.0:
                card_class = "alert-card-80"
                badge_color = "#ef4444"
                badge_text_color = "#ffffff"
                dir_icon = "🔴 -80% CRITICAL"
            elif event.threshold >= 70.0:
                card_class = "alert-card-70"
                badge_color = "#f59e0b"
                badge_text_color = "#0f172a"
                dir_icon = "🟡 -70% HIGH"
            else:
                card_class = "alert-card-60"
                badge_color = "#10b981"
                badge_text_color = "#0f172a"
                dir_icon = "🟢 -60% TRIGGER"

        spot_val = event.underlying_price if event.underlying_price > 0 else (state_manager.get_underlying_price(event.symbol) or 0.0)
        spot_html = f" | 📈 Spot: <strong>₹{spot_val:.2f}</strong>" if spot_val > 0 else ""
        expiry_html = f" | 🏷️ Expiry: <strong>{event.expiry}</strong>" if event.expiry else ""

        c_col, btn_col = st.columns([5.2, 0.9])
        with c_col:
            card_html = (
                f'<div class="{card_class}">'
                f'<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">'
                f'<div>'
                f'<strong style="font-size: 1.15rem; color: #f8fafc;">{event.display_title}</strong>'
                f'<span style="background: {badge_color}; color: {badge_text_color}; font-weight: 700; padding: 3px 8px; border-radius: 4px; margin-left: 8px; font-size: 0.85rem;">'
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

        with btn_col:
            st.markdown("<div style='margin-top: 14px;'></div>", unsafe_allow_html=True)
            key_id = event.instrument_key or event.symbol
            ts_str = int(event.current_timestamp.timestamp())
            pinned_map = getattr(engine, "pinned_trades", {})
            is_pinned = key_id in pinned_map
            if is_pinned:
                st.button("📌 Pinned", key=f"pinned_badge_{tab_key}_{key_id}_{event.threshold}_{ts_str}_{idx}", disabled=True, width='stretch')
            else:
                if st.button("📌 Pin", key=f"pin_action_{tab_key}_{key_id}_{event.threshold}_{ts_str}_{idx}", width='stretch', help="Lock trade to Pinned Active Watchlist"):
                    if hasattr(engine, "pin_trade"):
                        engine.pin_trade(event)
                    st.toast(f"📌 Pinned {event.display_title} to Active Watchlist!", icon="🎯")
                    st.rerun()

with tab_core:
    render_alert_cards(
        core_alerts_list,
        tab_key="core",
        empty_msg="No core high-value option crashes (≥ ₹1.00) detected yet.",
    )

with tab_penny:
    st.caption("📉 **Expiry Penny Stream:** Tracks sub-₹1.00 options decaying towards zero near monthly expiry.")
    render_alert_cards(
        penny_alerts_list,
        tab_key="penny",
        empty_msg="No sub-₹1.00 expiry penny decay alerts recorded yet.",
    )

st.divider()

# -------------------------------------------------------------------
# Option Chain Matrix Viewer (Zero-Lag Cached Snapshot + Live WebSocket Overlay)
# -------------------------------------------------------------------

st.subheader("📊 Live Option Chain Matrix")

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

if is_dhan_mode and engine.dhan_provider:
    if selected_stock not in engine.cached_real_chains or refresh_snapshot:
        if refresh_snapshot:
            load_dotenv(override=True)
            engine.dhan_provider.access_token = os.getenv("DHAN_ACCESS_TOKEN", engine.dhan_provider.access_token)
            engine.dhan_provider.last_error = None
        with st.spinner(f"Loading DhanHQ real option chain snapshot for {selected_stock}..."):
            real_chain = engine.dhan_provider.fetch_real_option_chain(selected_stock)
            if real_chain:
                engine.cached_real_chains[selected_stock] = real_chain
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

    cached_chain = engine.cached_real_chains.get(selected_stock)

    if cached_chain and cached_chain.get("strikes"):
        spot_price = state_manager.get_underlying_price(selected_stock) or cached_chain["spot_price"]
        spot_hist = state_manager.get_history(selected_stock)
        spot_icon = "🟢 " if len(spot_hist) >= 2 else ""
        expiry_date = cached_chain["expiry"]
        st.markdown(
            f"**Underlying Spot:** `{selected_stock}` @ **{spot_icon}₹{spot_price:.2f}** | "
            f"**Expiry:** `{expiry_date}` | **Source:** `DhanHQ Real Market Data` | "
            f"**Live Ticks Ingested:** `{engine.live_ticks_received:,}`"
        )

        for item in cached_chain["strikes"]:
            strike = item["strike"]
            ce_key = f"{selected_stock}_{expiry_date}_{strike:.0f}_CE"
            pe_key = f"{selected_stock}_{expiry_date}_{strike:.0f}_PE"

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
        if engine.dhan_provider and engine.dhan_provider.last_error:
            st.error(f"{engine.dhan_provider.last_error}")
        else:
            st.warning(f"Could not load option chain for {selected_stock} from Dhan API. Click '🔄 Refresh Data' to retry.")

else:
    # Simulation Mode Table
    dummy_p = engine.dummy_provider
    underlying_price = state_manager.get_underlying_price(selected_stock) or dummy_p.spot_prices.get(selected_stock, 0.0)
    strikes = dummy_p.get_strikes_for_symbol(selected_stock)

    st.markdown(f"**Underlying Spot:** `{selected_stock}` @ **₹{underlying_price:.2f}** | **Expiry:** `{dummy_p.expiry_date}` | **Source:** `Simulation Mode` | **Simulated Ticks:** `{engine.update_count * 3780:,}`")

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

table_placeholder = st.empty()
if chain_rows:
    df_chain = pd.DataFrame(chain_rows)
    table_placeholder.dataframe(df_chain, width='stretch', hide_index=True)
else:
    table_placeholder.empty()

# -------------------------------------------------------------------
# Auto-stream loop
# -------------------------------------------------------------------

if engine.is_streaming_active:
    if is_dhan_mode:
        if engine.dhan_provider and engine.dhan_provider.last_error:
            engine.stop_dhan_feed()
            st.rerun()
        elif is_market_open:
            time.sleep(refresh_speed)
            st.rerun()
    else:
        time.sleep(refresh_speed)
        st.rerun()
