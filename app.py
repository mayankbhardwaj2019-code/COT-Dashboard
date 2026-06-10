import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import re
from datetime import date

st.set_page_config(
    page_title="COT Positioning Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

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
.section-hdr{font-size:12px;font-weight:700;text-transform:uppercase;
             letter-spacing:.07em;color:#94a3b8;margin:1.2rem 0 .5rem;}
</style>
""", unsafe_allow_html=True)

# ── Constants ───────────────────────────────────────────────────────────────
PRODUCTS = {
    "Corn": "Grains", "Wheat": "Grains", "Soybeans": "Grains",
    "KC Wheat": "Grains", "MN Wheat": "Grains", "Soybean Oil": "Grains",
    "Soybean Meal": "Grains", "Canola": "Grains", "Rough Rice": "Grains",
    "Live Cattle": "Livestock", "Feeder Cattle": "Livestock", "Lean Hogs": "Livestock",
}

# Display name overrides (Wheat in PDF = CBOT Wheat in our dashboard)
DISPLAY_NAMES = {"Wheat": "CBOT Wheat"}

FLOW_META = {
    "Long Buildup":  {"cls": "flow-lb", "icon": "🟢", "desc": "Longs ↑ + OI ↑ — Bullish momentum building"},
    "Short Buildup": {"cls": "flow-sb", "icon": "🔴", "desc": "Shorts ↑ + OI ↑ — Bearish pressure building"},
    "Long Unwinding":{"cls": "flow-lu", "icon": "🟡", "desc": "Longs ↓ + OI ↓ — Bullish momentum fading"},
    "Short Covering":{"cls": "flow-sc", "icon": "🔵", "desc": "Shorts ↓ + OI ↓ — Potential price recovery"},
}

MM_COLORS = ["#185FA5","#1D9E75","#D85A30","#7F77DD","#BA7517","#D4537E","#0F6E56","#534AB7","#993C1D"]
PM_COLORS = ["#2E7D32","#854F0B","#1565C0","#3B6D11","#534AB7","#993C1D","#065f46","#78350f","#1e3a8a"]

# ── Flow logic ──────────────────────────────────────────────────────────────
def calc_flow(wc: float, oi_wc: float) -> str:
    if wc > 0 and oi_wc >= 0: return "Long Buildup"
    if wc < 0 and oi_wc <= 0: return "Long Unwinding"
    if wc < 0 and oi_wc >= 0: return "Short Buildup"
    return "Short Covering"

# ── PDF Parser ──────────────────────────────────────────────────────────────
def parse_cot_pdf(pdf_file) -> dict | None:
    """
    Parse an RJ O'Brien Disaggregated COT PDF using regex on raw text.
    Column order per row (12 numbers):
      0: PM net    1: PM wc
      2: SD net    3: SD wc
      4: MM net    5: MM wc
      6: MM long   7: MM short
      8: OR net    9: OR wc
      10: OI       11: OI wc
    """
    try:
        import pdfplumber
    except ImportError:
        st.error("pdfplumber not installed. Run: pip install pdfplumber")
        return None

    try:
        full_text = ""
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                full_text += (page.extract_text() or "") + "\n"

        if not full_text.strip():
            return None

        # ── Extract week-end date ──────────────────────────────────────────
        m = re.search(
            r'\d{1,2}/\d{1,2}/\d{2,4}\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{2,4})',
            full_text
        )
        if not m:
            # Fallback: look for "week ended MM/DD/YY" phrasing
            m2 = re.search(r'week end(?:ed|ing)\s+(\d{1,2}/\d{1,2}/\d{2,4})', full_text, re.I)
            if not m2:
                return None
            end_str = m2.group(1)
        else:
            end_str = m.group(1)

        parts = end_str.split("/")
        mo, dy, yr = int(parts[0]), int(parts[1]), int(parts[2])
        if yr < 100:
            yr += 2000
        week_date = date(yr, mo, dy).strftime("%d %b %y")

        # ── Parse each product row ─────────────────────────────────────────
        NUM = r'(-?[\d,]+)'
        # Sort longer names first to avoid "Wheat" matching inside "KC Wheat"
        sorted_prods = sorted(PRODUCTS.keys(), key=len, reverse=True)

        parsed_rows = {}
        for prod in sorted_prods:
            pattern = re.escape(prod) + r'(?:\*)?' + (r'\s+' + NUM) * 12
            hit = re.search(pattern, full_text)
            if hit:
                nums = [float(v.replace(",", "")) for v in hit.groups()]
                parsed_rows[prod] = nums

        if not parsed_rows:
            return None

        # ── Build entry dicts ──────────────────────────────────────────────
        def make_entry(prod, nums, net_i, wc_i):
            net   = int(nums[net_i])
            wc    = int(nums[wc_i])
            oi    = int(nums[10])
            oi_wc = int(nums[11])
            pct   = round(net / oi * 100, 1) if oi else 0.0
            display = DISPLAY_NAMES.get(prod, prod)
            return {
                "c":    display,
                "net":  net,
                "wc":   wc,
                "oi":   oi,
                "pct":  pct,
                "oiWc": oi_wc,
                "flow": calc_flow(wc, oi_wc),
            }

        grains_mm, grains_pm = [], []
        live_mm,   live_pm   = [], []

        for prod, nums in parsed_rows.items():
            group = PRODUCTS[prod]
            pm_e = make_entry(prod, nums, 0, 1)   # PM: cols 0,1
            mm_e = make_entry(prod, nums, 4, 5)   # MM: cols 4,5
            if group == "Grains":
                grains_pm.append(pm_e)
                grains_mm.append(mm_e)
            else:
                live_pm.append(pm_e)
                live_mm.append(mm_e)

        return {
            "date": week_date,
            "groups": {
                "Grains":    {"mm": grains_mm, "pm": grains_pm},
                "Livestock": {"mm": live_mm,   "pm": live_pm},
            },
        }

    except Exception as e:
        st.error(f"PDF parsing error: {e}")
        return None

# ── Session state ───────────────────────────────────────────────────────────
if "weeks" not in st.session_state:
    st.session_state.weeks = []

# ── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📊 COT Dashboard")
    st.caption("RJ O'Brien Disaggregated COT")
    st.divider()

    st.subheader("Upload weekly PDF")
    uploaded = st.file_uploader(
        "Upload RJ O'Brien COT PDF",
        type=["pdf"],
        help="Upload the weekly RJ O'Brien Disaggregated Futures & Options PDF.",
        label_visibility="collapsed",
    )

    if uploaded:
        with st.spinner("Parsing PDF…"):
            parsed = parse_cot_pdf(uploaded)
        if parsed:
            existing = [w["date"] for w in st.session_state.weeks]
            if parsed["date"] in existing:
                idx = existing.index(parsed["date"])
                st.session_state.weeks[idx] = parsed
                st.success(f"✅ Updated: {parsed['date']}")
            else:
                st.session_state.weeks.append(parsed)
                st.session_state.weeks.sort(
                    key=lambda w: pd.to_datetime(w["date"], format="%d %b %y"),
                    reverse=True,
                )
                st.success(f"✅ Added: {parsed['date']}")
        else:
            st.error("Could not parse PDF. Ensure this is an RJ O'Brien Disaggregated COT report.")

    st.divider()

    if st.session_state.weeks:
        n = len(st.session_state.weeks)
        st.caption(f"**{n}** week{'s' if n > 1 else ''} on record")

        if st.button("🗑 Clear all data", use_container_width=True):
            st.session_state.weeks = []
            st.rerun()

        st.divider()

        # CSV export
        rows = []
        for w in st.session_state.weeks:
            for grp_name, grp in w["groups"].items():
                for label, key in [("Managed Money", "mm"), ("Producer/Merchant", "pm")]:
                    for e in grp[key]:
                        rows.append({
                            "Date": w["date"], "Group": grp_name,
                            "Trader": label, "Product": e["c"],
                            "Net Position": e["net"], "Weekly Change": e["wc"],
                            "Open Interest": e["oi"], "% of OI": e["pct"],
                            "Flow Signal": e["flow"],
                        })
        st.download_button(
            "⬇ Download CSV",
            pd.DataFrame(rows).to_csv(index=False),
            "cot_data.csv", "text/csv",
            use_container_width=True,
        )

# ── No data state ────────────────────────────────────────────────────────────
if not st.session_state.weeks:
    st.title("COT Positioning Dashboard")
    st.info("👈 Upload a weekly RJ O'Brien COT PDF from the sidebar to get started.")
    st.markdown("""
**Supported format:** RJ O'Brien Disaggregated Futures & Options COT report (PDF)

**Products tracked:**
- **Grains:** Corn, CBOT Wheat, Soybeans, KC Wheat, MN Wheat, Soybean Oil, Soybean Meal, Canola, Rough Rice
- **Livestock:** Live Cattle, Feeder Cattle, Lean Hogs
    """)
    st.stop()

weeks = st.session_state.weeks

# ── Top controls ─────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
with c1:
    sel_date = st.selectbox("📅 Week", [w["date"] for w in weeks])
with c2:
    sel_group = st.selectbox("📦 Category", ["Grains", "Livestock"])

sel_week = next(w for w in weeks if w["date"] == sel_date)
grp = sel_week["groups"][sel_group]
all_prods = sorted({e["c"] for e in grp["mm"] + grp["pm"]})

with c3:
    sel_product = st.selectbox("🌾 Product", all_prods)
with c4:
    sel_metric = st.selectbox("📈 Chart metric",
                              ["Net position", "Weekly change", "% of OI"])

metric_key = {"Net position": "net", "Weekly change": "wc", "% of OI": "pct"}[sel_metric]
metric_label = {
    "net": "Net position (contracts)",
    "wc":  "Weekly change (contracts)",
    "pct": "% of open interest",
}[metric_key]

st.divider()

# ── Helper: metric card html ─────────────────────────────────────────────────
def card(label, value, unit="", bold=False):
    col = "pos" if (value or 0) >= 0 else "neg"
    if isinstance(value, float):
        fmt = f"{value:+.1f}%" if unit == "%" else f"{value:,.1f}"
    else:
        fmt = f"{value:+,.0f}" if bold else f"{value:,.0f}"
    return (f'<div class="metric-card">'
            f'<div class="metric-label">{label}</div>'
            f'<div class="metric-value {col}">{fmt}</div>'
            f'<div style="font-size:11px;color:#94a3b8;">{unit or "contracts"}</div>'
            f'</div>')

def flow_card(entry):
    m = FLOW_META[entry["flow"]]
    return (f'<div class="metric-card"><div class="metric-label">Flow signal</div>'
            f'<div class="{m["cls"]}" style="margin-top:4px;">{m["icon"]} {entry["flow"]}</div>'
            f'<div style="font-size:11px;color:#64748b;margin-top:6px;">{m["desc"]}</div></div>')

# ── MM panel ─────────────────────────────────────────────────────────────────
mm_e = next((e for e in grp["mm"] if e["c"] == sel_product), None)
pm_e = next((e for e in grp["pm"] if e["c"] == sel_product), None)

st.markdown('<div class="section-hdr">Managed Money — Speculative</div>', unsafe_allow_html=True)
if mm_e:
    cols = st.columns(5)
    with cols[0]: st.markdown(card("Net position",   mm_e["net"]),         unsafe_allow_html=True)
    with cols[1]: st.markdown(card("Weekly change",  mm_e["wc"],  bold=True), unsafe_allow_html=True)
    with cols[2]: st.markdown(card("Open interest",  mm_e["oi"],  unit="total contracts"), unsafe_allow_html=True)
    with cols[3]: st.markdown(card("% of OI",        mm_e["pct"], unit="%"), unsafe_allow_html=True)
    with cols[4]: st.markdown(flow_card(mm_e),                               unsafe_allow_html=True)

st.markdown('<div class="section-hdr">Producer / Merchant — Commercial</div>', unsafe_allow_html=True)
if pm_e:
    cols = st.columns(5)
    with cols[0]: st.markdown(card("Net position",   pm_e["net"]),           unsafe_allow_html=True)
    with cols[1]: st.markdown(card("Weekly change",  pm_e["wc"], bold=True), unsafe_allow_html=True)
    with cols[2]: st.markdown(card("Open interest",  pm_e["oi"], unit="total contracts"), unsafe_allow_html=True)
    with cols[3]: st.markdown(card("% of OI",        pm_e["pct"], unit="%"), unsafe_allow_html=True)
    with cols[4]: st.markdown(flow_card(pm_e),                               unsafe_allow_html=True)

st.divider()

# ── Flow analysis grid ────────────────────────────────────────────────────────
st.markdown('<div class="section-hdr">Market flow analysis — all products this week</div>',
            unsafe_allow_html=True)
fcols = st.columns(4)
for i, ftype in enumerate(["Long Buildup", "Short Buildup", "Long Unwinding", "Short Covering"]):
    with fcols[i]:
        m = FLOW_META[ftype]
        mm_p = [e["c"] for e in grp["mm"] if e["flow"] == ftype]
        pm_p = [e["c"] for e in grp["pm"] if e["flow"] == ftype]
        body = ""
        if mm_p:
            body += "<div style='font-size:11px;font-weight:700;color:#475569;margin-top:6px;'>Managed Money</div>"
            body += "".join(f"<div style='font-size:12px;padding:2px 0'>• {p}</div>" for p in mm_p)
        if pm_p:
            body += "<div style='font-size:11px;font-weight:700;color:#475569;margin-top:6px;'>Producer/Merchant</div>"
            body += "".join(f"<div style='font-size:12px;padding:2px 0'>• {p}</div>" for p in pm_p)
        if not mm_p and not pm_p:
            body = "<div style='font-size:12px;color:#94a3b8;margin-top:6px;'>None this week</div>"
        st.markdown(
            f'<div class="{m["cls"]}" style="min-height:110px;">'
            f'<div style="font-size:12px;font-weight:700;margin-bottom:4px;">{m["icon"]} {ftype}</div>'
            f'<div style="font-size:11px;opacity:.8;margin-bottom:4px;">{m["desc"]}</div>'
            f'{body}</div>',
            unsafe_allow_html=True,
        )

st.divider()

# ── Time-series chart ─────────────────────────────────────────────────────────
st.markdown('<div class="section-hdr">Positioning chart — time series</div>', unsafe_allow_html=True)

chart_col, ctrl_col = st.columns([4, 1])
with ctrl_col:
    chart_prods = st.multiselect("Products", options=all_prods,
                                 default=[sel_product])
    show_mm = st.checkbox("Managed Money",      value=True)
    show_pm = st.checkbox("Producer/Merchant",  value=True)

# Build sorted date axis
all_dates = sorted(
    [w["date"] for w in weeks],
    key=lambda d: pd.to_datetime(d, format="%d %b %y"),
)

fig = go.Figure()
for pi, prod in enumerate(chart_prods or [sel_product]):
    mm_vals, pm_vals = [], []
    for d in all_dates:
        w = next((x for x in weeks if x["date"] == d), None)
        g = w["groups"][sel_group] if w else None
        mm_e2 = next((e for e in (g["mm"] if g else []) if e["c"] == prod), None)
        pm_e2 = next((e for e in (g["pm"] if g else []) if e["c"] == prod), None)
        mm_vals.append(mm_e2[metric_key] if mm_e2 else None)
        pm_vals.append(pm_e2[metric_key] if pm_e2 else None)

    mc = MM_COLORS[pi % len(MM_COLORS)]
    pc = PM_COLORS[pi % len(PM_COLORS)]

    if show_mm:
        fig.add_trace(go.Scatter(
            x=all_dates, y=mm_vals, name=f"{prod} — MM",
            mode="lines+markers",
            line=dict(color=mc, width=2),
            marker=dict(size=6),
            hovertemplate=f"<b>{prod} MM</b><br>%{{x}}<br>{metric_label}: %{{y:,.1f}}<extra></extra>",
        ))
    if show_pm:
        fig.add_trace(go.Scatter(
            x=all_dates, y=pm_vals, name=f"{prod} — PM",
            mode="lines+markers",
            line=dict(color=pc, width=2, dash="dash"),
            marker=dict(size=6, symbol="diamond"),
            hovertemplate=f"<b>{prod} PM</b><br>%{{x}}<br>{metric_label}: %{{y:,.1f}}<extra></extra>",
        ))

fig.add_hline(y=0, line_width=1, line_dash="dot", line_color="rgba(0,0,0,0.2)")
fig.update_layout(
    title=dict(text=f"{', '.join(chart_prods or [sel_product])} — {metric_label}", font_size=14),
    xaxis=dict(title="Week ending", tickangle=-45, showgrid=True, gridcolor="#f1f5f9"),
    yaxis=dict(title=metric_label, showgrid=True, gridcolor="#f1f5f9"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    hovermode="x unified",
    plot_bgcolor="white", paper_bgcolor="white",
    margin=dict(l=60, r=20, t=60, b=60),
    height=430,
)

with chart_col:
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": True, "scrollZoom": True})
