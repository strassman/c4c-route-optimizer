# 🗺️ Conway for Congress — Yard Sign Route Optimizer

A Streamlit app that solves the Traveling Salesman Problem for campaign yard sign deliveries.

## What it does
- Takes volunteer home addresses and delivery addresses as input
- Geocodes all addresses using OpenStreetMap (free, no API key needed)
- Clusters deliveries to the nearest volunteer
- Optimizes each volunteer's route using nearest-neighbor + 2-opt TSP
- Displays an interactive map with color-coded routes
- Shows a step-by-step delivery list per volunteer

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Sharing with the campaign team

Deploy for free on [Streamlit Community Cloud](https://streamlit.io/cloud):
1. Push this folder to a GitHub repo
2. Go to share.streamlit.io → "New app" → point to your repo
3. Share the URL with Ethan and the team — no install needed!

## How to use
1. **Input tab** — enter volunteer names + addresses, then delivery addresses (bulk paste supported)
2. Click **Optimize Routes**
3. **Map tab** — interactive map with each volunteer's color-coded route
4. **Routes tab** — step-by-step directions table, exportable
