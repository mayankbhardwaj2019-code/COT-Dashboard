import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json
import re
from pathlib import Path
import pdfplumber

st.set_page_config(
    page_title="COT Positioning Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.block-container{padding-top:1.5rem;padding-bottom:1rem;}
.metric-card{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
             padding:16px 20px;margin-bottom:8px;}
.metric-label{font-size:11px;font-weight:600;text-transform:uppercase;
              letter-spacing:.06em;color:#64748b;margin-bottom:4px;}
.metric-value{font-size:24px;font-weight:500;line-height:1.2;}
.pos{color:#1e7e34;} .neg{color:#b02a37;}
.flow-lb{background:#d1fae5;border:1px solid #34d399;border-radius:8px;
         padding:10px 14px;font-size:13px;font-weight:600;color:#065f46;}
.flow-sb{background:#fee2e2;border:1px solid #f87171;border-radius:8px;
         padding:10px 14px;font-size:13px;font-weight:600;color:#7f1d1d;}
.flow-lu{background:#fef3c7;border:1px solid #fbbf24;border-radius:8px;
         padding:10px 14px;font-size:13px;font-weight:600;color:#78350f;}
.flow-sc{background:#dbeafe;border:1px solid #60a5fa;border-radius:8px;
         padding:10px 14px;font-size:13px;font-weight:600;color:#1e3a8a;}
.section-hdr{font-size:13px;font-weight:700;text-transform:uppercase;
             letter-spacing:.07em;color:#94a3b8;margin:1rem 0 .5rem;}
</style>
""", unsafe_allow_html=True)

# ── Flow logic ─────────────────────────────────────────────────────────────
FLOW_META = {
    "Long Buildup":  {"cls": "flow-lb", "icon": "🟢", "desc": "Longs ↑ + OI ↑ — Bullish momentum building"},
    "Short Buildup": {"cls": "flow-sb", "icon": "🔴", "desc": "Shorts ↑ + OI ↑ — Bearish pressure building"},
    "Long Unwinding":{"cls": "flow-lu", "icon": "🟡", "desc": "Longs ↓ + OI ↓ — Bullish momentum fading"},
    "Short Covering":{"cls": "flow-sc", "icon": "🔵", "desc": "Shorts ↓ + OI ↓ — Potential price recovery"},
}

def calc_flow(wc, oi_wc):
    if wc > 0 and oi_wc >= 0: return "Long Buildup"
    if wc < 0 and oi_wc <= 0: return "Long Unwinding"
    if wc < 0 and oi_wc >= 0: return "Short Buildup"
    return "Short Covering"

# ── PDF Parser ─────────────────────────────────────────────────────────────
GRAINS    = ["Corn", "Wheat", "Soybeans", "KC Wheat", "MN Wheat",
             "Soybean Oil", "Soybean Meal", "Canola", "Rough Rice"]
LIVESTOCK = ["Live Cattle", "Feeder Cattle", "Lean Hogs"]
ALL_PRODUCTS = GRAINS + LIVESTOCK

def parse_cot_pdf(pdf_file) -> dict | None:
    """
    Extract COT data from an RJ O'Brien disaggregated PDF.
    Returns a dict with keys: date, groups {Grains: {mm:[], pm:[]}, Livestock: {mm:[], pm:[]}}
    """
    try:
        rows = {}
        week_date = None

        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""

                # Extract date range from header like "11/03/2025 - 11/10/25"
                if week_date is None:
                    m = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{2,4})', text)
                    if m:
                        end_str = m.group(2)
                        parts = end_str.split("/")
                        mo, dy = int(parts[0]), int(parts[1])
                        yr = int(parts[2])
                        if yr < 100:
                            yr += 2000
                        from datetime import date
                        week_date = date(yr, mo, dy).strftime("%d %b %y")

                # Extract table rows via pdfplumber
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if not row or not row[0]:
                            continue
                        name = str(row[0]).strip()
                        # Match known product names (partial match)
                        matched = None
                        for prod in ALL_PRODUCTS:
                            if prod.lower() in name.lower() or name.lower() in prod.lower():
                                matched = prod
                                break
                        if not matched:
                            continue

                        # Clean numeric cells
                        def n(val):
                            if val is None: return None
                            s = str(val).replace(",", "").replace("(", "-").replace(")", "").strip()
                            try: return float(s)
                            except: return None

                        nums = [n(c) for c in row[1:] if c is not None and str(c).strip() != ""]
                        # We need at least 10 numbers:
                        # PM_net PM_wc  SD_net SD_wc  MM_net MM_wc  MM_long MM_short  OR_net OR_wc  OI OI_wc
                        if len(nums) < 10:
                            continue

                        rows[matched] = nums

        if not rows or week_date is None:
            return None

        def build_entry(prod, nums, trader_idx_net, trader_idx_wc, oi_idx, oi_wc_idx):
            net = nums[trader_idx_net] if len(nums) > trader_idx_net else None
            wc  = nums[trader_idx_wc]  if len(nums) > trader_idx_wc  else None
            oi  = nums[oi_idx]         if len(nums) > oi_idx         else None
            oi_wc = nums[oi_wc_idx]    if len(nums) > oi_wc_idx      else None
            if net is None or wc is None or oi is None: return None
            pct = round(net / oi * 100, 1) if oi else 0
            flow = calc_flow(wc, oi_wc or 0)
            return {"c": prod, "net": int(net), "wc": int(wc), "oi": int(oi),
                    "pct": pct, "oiWc": int(oi_wc or 0), "flow": flow}

        grains_mm, grains_pm = [], []
        live_mm,   live_pm   = [], []

        for prod, nums in rows.items():
            # Column layout (0-indexed after product name removed):
            # 0:PM_net 1:PM_wc  2:SD_net 3:SD_wc  4:MM_net 5:MM_wc
            # 6:MM_long 7:MM_short  8:OR_net 9:OR_wc  10:OI 11:OI_wc
            oi_idx    = 10 if len(nums) > 10 else len(nums) - 2
            oi_wc_idx = 11 if len(nums) > 11 else len(nums) - 1

            pm_e = build_entry(prod, nums, 0, 1, oi_idx, oi_wc_idx)
            mm_e = build_entry(prod, nums, 4, 5, oi_idx, oi_wc_idx)

            if prod in GRAINS:
                if mm_e: grains_mm.append(mm_e)
                if pm_e: grains_pm.append(pm_e)
            elif prod in LIVESTOCK:
                if mm_e: live_mm.append(mm_e)
                if pm_e: live_pm.append(pm_e)

        return {
            "date": week_date,
            "groups": {
                "Grains":    {"mm": grains_mm, "pm": grains_pm},
                "Livestock": {"mm": live_mm,   "pm": live_pm},
            }
        }

    except Exception as e:
        st.error(f"PDF parsing error: {e}")
        return None

# ── Session state ──────────────────────────────────────────────────────────
if "weeks" not in st.session_state:
    st.session_state.weeks = []

if "hidden_series" not in st.session_state:
    st.session_state.hidden_series = set()

# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📊 COT Dashboard")
    st.caption("RJ O'Brien Disaggregated COT")
    st.divider()

    st.subheader("Upload weekly PDF")
    uploaded = st.file_uploader(
        "Upload RJ O'Brien COT PDF",
        type=["pdf"],
        help="Upload the weekly RJ O'Brien COT disaggregated PDF. Data is parsed automatically.",
        label_visibility="collapsed",
    )

    if uploaded:
        with st.spinner("Parsing PDF…"):
            parsed = parse_cot_pdf(uploaded)
        if parsed:
            existing_dates = [w["date"] for w in st.session_state.weeks]
            if parsed["date"] in existing_dates:
                idx = existing_dates.index(parsed["date"])
                st.session_state.weeks[idx] = parsed
                st.success(f"Updated week: {parsed['date']}")
            else:
                st.session_state.weeks.append(parsed)
                st.session_state.weeks.sort(
                    key=lambda w: pd.to_datetime(w["date"], format="%d %b %y"),
                    reverse=True
                )
                st.success(f"Added week: {parsed['date']}")
        else:
            st.error("Could not parse PDF. Check format.")

    st.divider()

    if st.session_state.weeks:
        st.caption(f"**{len(st.session_state.weeks)}** week(s) on record")
        if st.button("🗑 Clear all data", use_container_width=True):
            st.session_state.weeks = []
            st.rerun()

        st.divider()
        st.subheader("Export data")
        all_rows = []
        for w in st.session_state.weeks:
            for grp_name, grp in w["groups"].items():
                for trader, key in [("Managed Money","mm"),("Producer/Merchant","pm")]:
                    for e in grp[key]:
                        all_rows.append({
                            "Date": w["date"], "Group": grp_name,
                            "Trader": trader, "Product": e["c"],
                            "Net Position": e["net"], "Weekly Change": e["wc"],
                            "Open Interest": e["oi"], "% of OI": e["pct"],
                            "Flow Signal": e["flow"],
                        })
        if all_rows:
            df_export = pd.DataFrame(all_rows)
            st.download_button(
                "⬇ Download CSV",
                df_export.to_csv(index=False),
                "cot_data.csv", "text/csv",
                use_container_width=True,
            )

# ── Main content ───────────────────────────────────────────────────────────
if not st.session_state.weeks:
    st.title("COT Positioning Dashboard")
    st.info("👈 Upload a weekly RJ O'Brien COT PDF from the sidebar to get started.")
    st.markdown("""
    **What this dashboard shows:**
    - Managed Money and Producer/Merchant net positions
    - Weekly change, Open Interest, % of OI
    - Flow signal classification (Long Buildup, Short Buildup, Long Unwinding, Short Covering)
    - Continuous time-series charts with click-to-toggle series

    **Supported PDF format:** RJ O'Brien Disaggregated Futures & Options COT report
    """)
    st.stop()

weeks = st.session_state.weeks

# ── Controls row ───────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns([2, 2, 2, 2])

with col1:
    week_dates = [w["date"] for w in weeks]
    sel_date = st.selectbox("📅 Week", week_dates, index=0)

with col2:
    sel_group = st.selectbox("📦 Category", ["Grains", "Livestock"])

sel_week = next(w for w in weeks if w["date"] == sel_date)
grp = sel_week["groups"][sel_group]
all_products = list({e["c"] for e in grp["mm"] + grp["pm"]})

with col3:
    sel_product = st.selectbox("🌾 Product", sorted(all_products))

with col4:
    sel_metric = st.selectbox("📈 Chart metric", ["Net position", "Weekly change", "% of OI"])

st.divider()

# ── Metric cards ───────────────────────────────────────────────────────────
mm_entry = next((e for e in grp["mm"] if e["c"] == sel_product), None)
pm_entry = next((e for e in grp["pm"] if e["c"] == sel_product), None)

def fmt_n(n): return f"{n:+,.0f}" if n else "—"
def fmt_p(p): return f"{p:+.1f}%" if p is not None else "—"
def val_color(v): return "pos" if (v or 0) >= 0 else "neg"

st.markdown('<div class="section-hdr">Managed Money — Speculative</div>', unsafe_allow_html=True)

if mm_entry:
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        v = mm_entry["net"]
        st.markdown(f'<div class="metric-card"><div class="metric-label">Net position</div>'
                    f'<div class="metric-value {val_color(v)}">{v:,.0f}</div>'
                    f'<div style="font-size:11px;color:#94a3b8;">contracts</div></div>', unsafe_allow_html=True)
    with c2:
        v = mm_entry["wc"]
        st.markdown(f'<div class="metric-card"><div class="metric-label">Weekly change</div>'
                    f'<div class="metric-value {val_color(v)}">{fmt_n(v)}</div>'
                    f'<div style="font-size:11px;color:#94a3b8;">contracts</div></div>', unsafe_allow_html=True)
    with c3:
        v = mm_entry["oi"]
        st.markdown(f'<div class="metric-card"><div class="metric-label">Open interest</div>'
                    f'<div class="metric-value">{v:,.0f}</div>'
                    f'<div style="font-size:11px;color:#94a3b8;">total contracts</div></div>', unsafe_allow_html=True)
    with c4:
        v = mm_entry["pct"]
        st.markdown(f'<div class="metric-card"><div class="metric-label">% of OI</div>'
                    f'<div class="metric-value {val_color(v)}">{fmt_p(v)}</div>'
                    f'<div style="font-size:11px;color:#94a3b8;">net position share</div></div>', unsafe_allow_html=True)
    with c5:
        flow = mm_entry["flow"]
        m = FLOW_META[flow]
        st.markdown(f'<div class="metric-card"><div class="metric-label">Flow signal</div>'
                    f'<div class="{m["cls"]}" style="margin-top:4px;">{m["icon"]} {flow}</div>'
                    f'<div style="font-size:11px;color:#64748b;margin-top:6px;">{m["desc"]}</div></div>',
                    unsafe_allow_html=True)

st.markdown('<div class="section-hdr">Producer / Merchant — Commercial</div>', unsafe_allow_html=True)

if pm_entry:
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        v = pm_entry["net"]
        st.markdown(f'<div class="metric-card"><div class="metric-label">Net position</div>'
                    f'<div class="metric-value {val_color(v)}">{v:,.0f}</div>'
                    f'<div style="font-size:11px;color:#94a3b8;">contracts</div></div>', unsafe_allow_html=True)
    with c2:
        v = pm_entry["wc"]
        st.markdown(f'<div class="metric-card"><div class="metric-label">Weekly change</div>'
                    f'<div class="metric-value {val_color(v)}">{fmt_n(v)}</div>'
                    f'<div style="font-size:11px;color:#94a3b8;">contracts</div></div>', unsafe_allow_html=True)
    with c3:
        v = pm_entry["oi"]
        st.markdown(f'<div class="metric-card"><div class="metric-label">Open interest</div>'
                    f'<div class="metric-value">{v:,.0f}</div>'
                    f'<div style="font-size:11px;color:#94a3b8;">total contracts</div></div>', unsafe_allow_html=True)
    with c4:
        v = pm_entry["pct"]
        st.markdown(f'<div class="metric-card"><div class="metric-label">% of OI</div>'
                    f'<div class="metric-value {val_color(v)}">{fmt_p(v)}</div>'
                    f'<div style="font-size:11px;color:#94a3b8;">net position share</div></div>', unsafe_allow_html=True)
    with c5:
        flow = pm_entry["flow"]
        m = FLOW_META[flow]
        st.markdown(f'<div class="metric-card"><div class="metric-label">Flow signal</div>'
                    f'<div class="{m["cls"]}" style="margin-top:4px;">{m["icon"]} {flow}</div>'
                    f'<div style="font-size:11px;color:#64748b;margin-top:6px;">{m["desc"]}</div></div>',
                    unsafe_allow_html=True)

st.divider()

# ── Flow analysis grid ─────────────────────────────────────────────────────
st.markdown('<div class="section-hdr">Market flow analysis — all products this week</div>', unsafe_allow_html=True)
flow_types = ["Long Buildup", "Short Buildup", "Long Unwinding", "Short Covering"]
fcols = st.columns(4)
for i, ftype in enumerate(flow_types):
    with fcols[i]:
        m = FLOW_META[ftype]
        mm_prods = [e["c"] for e in grp["mm"] if e["flow"] == ftype]
        pm_prods = [e["c"] for e in grp["pm"] if e["flow"] == ftype]
        body = ""
        if mm_prods:
            body += f"<div style='font-size:11px;font-weight:600;color:#64748b;margin-top:6px;'>Managed Money</div>"
            body += "".join(f"<div style='font-size:12px;padding:2px 0;'>• {p}</div>" for p in mm_prods)
        if pm_prods:
            body += f"<div style='font-size:11px;font-weight:600;color:#64748b;margin-top:6px;'>Producer/Merchant</div>"
            body += "".join(f"<div style='font-size:12px;padding:2px 0;'>• {p}</div>" for p in pm_prods)
        if not mm_prods and not pm_prods:
            body = "<div style='font-size:12px;color:#94a3b8;margin-top:6px;'>None this week</div>"
        st.markdown(
            f'<div class="{m["cls"]}" style="min-height:120px;">'
            f'<div style="font-size:12px;font-weight:700;margin-bottom:4px;">{m["icon"]} {ftype}</div>'
            f'<div style="font-size:11px;opacity:.8;margin-bottom:4px;">{m["desc"]}</div>'
            f'{body}</div>',
            unsafe_allow_html=True
        )

st.divider()

# ── Time-series chart ──────────────────────────────────────────────────────
st.markdown('<div class="section-hdr">Positioning chart — time series</div>', unsafe_allow_html=True)

chart_col1, chart_col2 = st.columns([3, 1])
with chart_col2:
    chart_products = st.multiselect(
        "Products to plot",
        options=sorted(all_products),
        default=[sel_product],
        help="Select one or more products to overlay on the chart"
    )
    show_mm = st.checkbox("Managed Money", value=True)
    show_pm = st.checkbox("Producer / Merchant", value=True)

metric_key = {"Net position": "net", "Weekly change": "wc", "% of OI": "pct"}[sel_metric]
metric_label = {"net": "Net position (contracts)", "wc": "Weekly change (contracts)", "pct": "% of open interest"}[metric_key]

MM_COLORS = ["#185FA5","#1D9E75","#D85A30","#7F77DD","#BA7517","#D4537E","#0F6E56","#534AB7","#993C1D"]
PM_COLORS = ["#2E7D32","#854F0B","#185FA5","#3B6D11","#534AB7","#993C1D","#065f46","#78350f","#1e3a8a"]

all_dates_sorted = sorted(
    [w["date"] for w in weeks],
    key=lambda d: pd.to_datetime(d, format="%d %b %y")
)

fig = go.Figure()

for pi, prod in enumerate(chart_products or [sel_product]):
    mm_vals, pm_vals = [], []
    for d in all_dates_sorted:
        w = next((x for x in weeks if x["date"] == d), None)
        if w:
            g = w["groups"][sel_group]
            mm_e = next((e for e in g["mm"] if e["c"] == prod), None)
            pm_e = next((e for e in g["pm"] if e["c"] == prod), None)
            mm_vals.append(mm_e[metric_key] if mm_e else None)
            pm_vals.append(pm_e[metric_key] if pm_e else None)
        else:
            mm_vals.append(None)
            pm_vals.append(None)

    mc = MM_COLORS[pi % len(MM_COLORS)]
    pc = PM_COLORS[pi % len(PM_COLORS)]

    if show_mm:
        fig.add_trace(go.Scatter(
            x=all_dates_sorted, y=mm_vals,
            name=f"{prod} — MM",
            mode="lines+markers",
            line=dict(color=mc, width=2),
            marker=dict(size=6),
            hovertemplate=f"<b>{prod} MM</b><br>%{{x}}<br>{metric_label}: %{{y:,.1f}}<extra></extra>",
        ))
    if show_pm:
        fig.add_trace(go.Scatter(
            x=all_dates_sorted, y=pm_vals,
            name=f"{prod} — PM",
            mode="lines+markers",
            line=dict(color=pc, width=2, dash="dash"),
            marker=dict(size=6, symbol="diamond"),
            hovertemplate=f"<b>{prod} PM</b><br>%{{x}}<br>{metric_label}: %{{y:,.1f}}<extra></extra>",
        ))

fig.add_hline(y=0, line_width=1, line_dash="dot", line_color="rgba(0,0,0,0.25)")

fig.update_layout(
    title=dict(
        text=f"{', '.join(chart_products or [sel_product])} — {metric_label}",
        font=dict(size=14),
    ),
    xaxis=dict(title="Week ending", tickangle=-45, showgrid=True, gridcolor="rgba(0,0,0,0.06)"),
    yaxis=dict(title=metric_label, showgrid=True, gridcolor="rgba(0,0,0,0.06)"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    hovermode="x unified",
    plot_bgcolor="white",
    paper_bgcolor="white",
    margin=dict(l=60, r=20, t=60, b=60),
    height=420,
)

with chart_col1:
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": True})
