import asyncio
import time
from datetime import datetime

import pandas as pd
import streamlit as st

from app.alerts.manager import AlertManager
from app.data.dummy import DummyMarketDataProvider
from app.data.models import OptionTick, OptionType
from app.detection.detector import ExtremeDetector
from app.movement.calculator import calculate_movement
from app.state.manager import StateManager


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
        background: rgba(34, 197, 94, 0.12);
        border-left: 5px solid #22c55e;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 10px;
    }
    .alert-card-down {
        background: rgba(239, 68, 68, 0.12);
        border-left: 5px solid #ef4444;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 10px;
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

if "provider" not in st.session_state:
    st.session_state.provider = DummyMarketDataProvider(
        update_interval_seconds=0,
        strikes_above_below=4,  # 9 strikes per stock -> 18 contracts per stock -> ~3,780 total contracts
        enable_random_spikes=True,
    )

if "state_manager" not in st.session_state:
    st.session_state.state_manager = StateManager(window_minutes=60)

if "detector" not in st.session_state:
    st.session_state.detector = ExtremeDetector(thresholds=(60.0, 70.0, 80.0))

if "alert_manager" not in st.session_state:
    st.session_state.alert_manager = AlertManager()

if "recent_alerts" not in st.session_state:
    st.session_state.recent_alerts = []

if "update_count" not in st.session_state:
    st.session_state.update_count = 0

if "auto_stream" not in st.session_state:
    st.session_state.auto_stream = False

provider: DummyMarketDataProvider = st.session_state.provider
state_manager: StateManager = st.session_state.state_manager
detector: ExtremeDetector = st.session_state.detector
alert_manager: AlertManager = st.session_state.alert_manager

total_contracts = len(provider.symbols) * (provider.strikes_above_below * 2 + 1) * 2

# -------------------------------------------------------------------
# Header & Metrics
# -------------------------------------------------------------------

h_col1, h_col2 = st.columns([3, 1])
with h_col1:
    st.markdown('<h1 class="main-header">⚡ F&O Option Chain Extreme Movement Monitor</h1>', unsafe_allow_html=True)
    st.caption("Live Real-Time Scanner — Monitoring All 210 F&O Stock Option Chains for ±60%, ±70%, ±80% Jumps")
with h_col2:
    if st.session_state.auto_stream:
        st.markdown('<div style="text-align: right; margin-top: 15px;"><span class="live-badge live-active">● STREAMING LIVE</span></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="text-align: right; margin-top: 15px;"><span class="live-badge live-paused">⏸️ STREAM PAUSED</span></div>', unsafe_allow_html=True)

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("F&O Stocks Tracked", f"{len(provider.symbols)} Stocks")
with col2:
    st.metric("Option Contracts", f"{total_contracts:,} CE/PE")
with col3:
    st.metric("Detection Window", "60 min Rolling")
with col4:
    st.metric("Alert Thresholds", "±60%, ±70%, ±80%")
with col5:
    st.metric("Active Alerts Triggered", f"{len(st.session_state.recent_alerts)}")

st.divider()

# -------------------------------------------------------------------
# Streaming Controls
# -------------------------------------------------------------------

ctrl1, ctrl2, ctrl3, ctrl4, ctrl5 = st.columns([2, 2, 2, 2, 1.5])

with ctrl1:
    auto_stream_toggle = st.toggle("🟢 Live Auto-Streaming", value=st.session_state.auto_stream)
    if auto_stream_toggle != st.session_state.auto_stream:
        st.session_state.auto_stream = auto_stream_toggle
        st.rerun()

with ctrl2:
    refresh_speed = st.selectbox(
        "Stream Speed",
        options=[1, 2, 3, 5],
        index=1,
        format_func=lambda x: f"⚡ {x}s / batch (3,780 ticks)",
    )

with ctrl3:
    run_single_batch = st.button("▶ Step One Batch", width='stretch', disabled=st.session_state.auto_stream)

with ctrl4:
    inject_spike = st.button("💥 Inject Random Spike", width='stretch')

with ctrl5:
    if st.button("🗑 Clear State", width='stretch'):
        state_manager.clear()
        alert_manager.clear()
        st.session_state.recent_alerts = []
        st.session_state.update_count = 0
        st.success("State cleared.")
        st.rerun()

# -------------------------------------------------------------------
# Execution Engine (Single Batch or Continuous Stream)
# -------------------------------------------------------------------

should_process = st.session_state.auto_stream or run_single_batch or inject_spike

if inject_spike:
    spiked_key = provider.inject_extreme_spike(percentage_change=75.0)
    st.toast(f"⚡ Injected +75% surge into contract: {spiked_key}!", icon="🚀")

if should_process:
    ticks = provider.generate_option_ticks()
    st.session_state.update_count += 1
    new_alerts = []

    for tick in ticks:
        state_manager.update(tick)
        history = state_manager.get_history(tick.instrument_key)

        if len(history) < 2:
            continue

        movement = calculate_movement(history)
        if movement is None:
            continue

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

    if new_alerts:
        st.session_state.recent_alerts = new_alerts + st.session_state.recent_alerts
        st.session_state.recent_alerts = st.session_state.recent_alerts[:100]
        st.toast(f"🚨 {len(new_alerts)} New Extreme Movement Alert(s) Detected!", icon="⚡")

# -------------------------------------------------------------------
# Top Market Movers Bar (Real-Time Scanner)
# -------------------------------------------------------------------

movers = []
for symbol in provider.symbols:
    for key in state_manager.get_instruments_for_symbol(symbol):
        hist = state_manager.get_history(key)
        if len(hist) >= 2:
            pct = ((hist[-1].price - hist[0].price) / hist[0].price) * 100
            if abs(pct) >= 5.0:  # Noticeable moves
                # Parse readable title from key
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
        badge_color = "#22c55e" if is_up else "#ef4444"
        dir_icon = "🟢 UP" if is_up else "🔴 DOWN"
        card_class = "alert-card-up" if is_up else "alert-card-down"

        st.markdown(
            f"""
            <div class="{card_class}">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <strong style="font-size: 1.15rem; color: #f8fafc;">{event.display_title}</strong>
                        <span style="background: {badge_color}; color: black; font-weight: 700; padding: 2px 8px; border-radius: 4px; margin-left: 8px;">
                            {event.percentage_change:+.2f}% ({dir_icon})
                        </span>
                        <span style="color: #94a3b8; margin-left: 8px;">Threshold: {event.threshold:.0f}%</span>
                    </div>
                    <div style="font-size: 0.9rem; color: #cbd5e1;">
                        ⏱️ {event.current_timestamp.strftime('%H:%M:%S')} ({event.duration_seconds/60:.1f}m window)
                    </div>
                </div>
                <div style="margin-top: 6px; font-size: 0.95rem; color: #cbd5e1;">
                    💰 Premium: <strong>₹{event.start_price:.2f}</strong> ➔ <strong>₹{event.current_price:.2f}</strong>
                    {' | 📈 Spot: ₹' + f'{event.underlying_price:.2f}' if event.underlying_price > 0 else ''}
                    | 🏷️ Expiry: {event.expiry}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
else:
    st.info("No extreme option premium jumps detected yet. Turn ON **'🟢 Live Auto-Streaming'** to scan in real time.")

st.divider()

# -------------------------------------------------------------------
# Option Chain Matrix Viewer
# -------------------------------------------------------------------

st.subheader("📊 Live Option Chain Matrix")

selected_stock = st.selectbox(
    "Select Stock to View Option Chain",
    provider.symbols,
    index=provider.symbols.index("RELIANCE") if "RELIANCE" in provider.symbols else 0,
)

underlying_price = state_manager.get_underlying_price(selected_stock) or provider.spot_prices.get(selected_stock, 0.0)
strikes = provider.get_strikes_for_symbol(selected_stock)

st.markdown(f"**Underlying Spot:** `{selected_stock}` @ **₹{underlying_price:.2f}** | **Expiry:** `{provider.expiry_date}` | **Batches Streamed:** `{st.session_state.update_count}`")

chain_rows = []
for strike in strikes:
    ce_key = f"{selected_stock}_{provider.expiry_date}_{strike:.0f}_CE"
    pe_key = f"{selected_stock}_{provider.expiry_date}_{strike:.0f}_PE"

    ce_obs = state_manager.get_latest(ce_key)
    pe_obs = state_manager.get_latest(pe_key)

    ce_price = ce_obs.price if ce_obs else provider.premiums.get(ce_key, 0.0)
    ce_vol = ce_obs.volume if ce_obs else provider.volumes.get(ce_key, 0)
    ce_oi = ce_obs.open_interest if ce_obs else provider.open_interests.get(ce_key, 0)

    pe_price = pe_obs.price if pe_obs else provider.premiums.get(pe_key, 0.0)
    pe_vol = pe_obs.volume if pe_obs else provider.volumes.get(pe_key, 0)
    pe_oi = pe_obs.open_interest if pe_obs else provider.open_interests.get(pe_key, 0)

    ce_hist = state_manager.get_history(ce_key)
    pe_hist = state_manager.get_history(pe_key)
    ce_chg = f"{((ce_price - ce_hist[0].price)/ce_hist[0].price)*100:+.1f}%" if len(ce_hist) >= 2 else "0.0%"
    pe_chg = f"{((pe_price - pe_hist[0].price)/pe_hist[0].price)*100:+.1f}%" if len(pe_hist) >= 2 else "0.0%"

    chain_rows.append(
        {
            "CE OI": f"{ce_oi:,}",
            "CE Volume": f"{ce_vol:,}",
            "CE Chg %": ce_chg,
            "CALL (CE) LTP (₹)": f"₹{ce_price:.2f}",
            "🎯 STRIKE (₹)": f"₹{strike:.0f}",
            "PUT (PE) LTP (₹)": f"₹{pe_price:.2f}",
            "PE Chg %": pe_chg,
            "PE Volume": f"{pe_vol:,}",
            "PE OI": f"{pe_oi:,}",
        }
    )

if chain_rows:
    df_chain = pd.DataFrame(chain_rows)
    st.dataframe(df_chain, width='stretch', hide_index=True)

# -------------------------------------------------------------------
# Auto-stream loop
# -------------------------------------------------------------------

if st.session_state.auto_stream:
    time.sleep(refresh_speed)
    st.rerun()

