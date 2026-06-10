# COT Positioning Dashboard

Interactive Streamlit dashboard for CFTC Commitments of Traders (COT) data, built around the **RJ O'Brien Disaggregated Futures & Options** weekly report.

## Features

- **PDF upload** — upload any RJ O'Brien COT PDF; data is parsed and added automatically
- **Managed Money & Producer/Merchant** — separate panels for each trader type
- **Four metrics** — Net position, Weekly change, Open Interest, % of OI
- **Flow signal** — auto-classifies each product as Long Buildup, Short Buildup, Long Unwinding, or Short Covering
- **Time-series chart** — multi-product overlay with click-to-toggle series (Plotly)
- **CSV export** — download all stored data as CSV
- **Persistent session** — all uploaded weeks stay in memory for the session

## Products covered

**Grains:** Corn, Wheat, Soybeans, KC Wheat, MN Wheat, Soybean Oil, Soybean Meal, Canola, Rough Rice  
**Livestock:** Live Cattle, Feeder Cattle, Lean Hogs

## Local setup

```bash
git clone https://github.com/<your-username>/cot-dashboard.git
cd cot-dashboard
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Streamlit Community Cloud

1. Push this repo to GitHub (public or private)
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Click **New app** → select your repo → set **Main file path** to `app.py`
4. Click **Deploy**

Your app will be live at `https://<your-app>.streamlit.app`

## PDF format

Upload the weekly **RJ O'Brien Commitments of Traders Report – Disaggregated Futures and Options** PDF.  
The parser reads the date range from the header (e.g. `11/03/2025 - 11/10/25`) and extracts:

| Column | Source |
|--------|--------|
| Producer/Merchant net & weekly change | Columns 1–2 |
| Managed Money net & weekly change | Columns 5–6 |
| Open Interest & weekly change | Last 2 columns |

## Flow signal logic

| Signal | Condition |
|--------|-----------|
| Long Buildup | MM/PM weekly change > 0 AND OI weekly change ≥ 0 |
| Short Buildup | MM/PM weekly change < 0 AND OI weekly change ≥ 0 |
| Long Unwinding | MM/PM weekly change < 0 AND OI weekly change ≤ 0 |
| Short Covering | MM/PM weekly change > 0 AND OI weekly change < 0 |
