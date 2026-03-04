import streamlit as st
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import pandas as pd
import math
import time

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="C4C Route Optimizer",
    page_icon="🗺️",
    layout="wide",
)

COLORS = ["red", "blue", "green", "orange", "purple", "darkred", "cadetblue", "darkgreen"]
HEX_COLORS = ["#e74c3c","#3498db","#2ecc71","#f39c12","#9b59b6","#c0392b","#5f9ea0","#27ae60"]

# ── TSP helpers ────────────────────────────────────────────────────────────────
def haversine(a, b):
    """Distance in km between two (lat, lng) tuples."""
    R = 6371
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(h))

def nearest_neighbor(points):
    n = len(points)
    if n <= 1:
        return list(range(n))
    visited = [False] * n
    route = [0]
    visited[0] = True
    for _ in range(n - 1):
        last = route[-1]
        best, best_d = -1, float("inf")
        for j in range(n):
            if not visited[j]:
                d = haversine(points[last], points[j])
                if d < best_d:
                    best_d, best = d, j
        route.append(best)
        visited[best] = True
    return route

def two_opt(points, route):
    improved = True
    while improved:
        improved = False
        for i in range(1, len(route) - 1):
            for j in range(i + 1, len(route)):
                a, b = route[i - 1], route[i]
                c, d = route[j], route[(j + 1) % len(route)]
                before = haversine(points[a], points[b]) + haversine(points[c], points[d])
                after  = haversine(points[a], points[c]) + haversine(points[b], points[d])
                if after < before - 1e-10:
                    route[i:j + 1] = route[i:j + 1][::-1]
                    improved = True
    return route

def solve_tsp(coords):
    """Return optimized index order for a list of (lat, lng) coords."""
    if len(coords) <= 1:
        return list(range(len(coords)))
    route = nearest_neighbor(coords)
    route = two_opt(coords, route)
    return route

def total_distance(coords, route):
    d = sum(haversine(coords[route[i]], coords[route[i+1]]) for i in range(len(route)-1))
    d += haversine(coords[route[-1]], coords[route[0]])
    return round(d, 2)

# ── Geocoding ──────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def geocode_address(address: str):
    geolocator = Nominatim(user_agent="c4c_route_optimizer")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
    loc = geocode(address)
    if loc is None:
        return None, None
    return loc.latitude, loc.longitude

# ── Clustering ─────────────────────────────────────────────────────────────────
def assign_to_volunteer(delivery_coord, volunteer_coords):
    dists = [haversine(delivery_coord, vc) for vc in volunteer_coords]
    return dists.index(min(dists))

# ── UI ─────────────────────────────────────────────────────────────────────────
st.title("🗺️ Conway for Congress — Yard Sign Route Optimizer")
st.caption("Enter volunteer and delivery addresses. The app clusters deliveries to the nearest volunteer and optimizes each route.")

tab_input, tab_map, tab_routes = st.tabs(["📋 Input", "🗺️ Map", "📍 Routes"])

with tab_input:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("👤 Volunteers")
        st.caption("Add each volunteer's name and home address.")

        if "volunteers" not in st.session_state:
            st.session_state.volunteers = [{"name": "", "address": ""}]

        for i, v in enumerate(st.session_state.volunteers):
            with st.container(border=True):
                color_dot = f'<span style="color:{HEX_COLORS[i % len(HEX_COLORS)]};font-size:18px;">●</span>'
                st.markdown(f"{color_dot} **Volunteer {i+1}**", unsafe_allow_html=True)
                st.session_state.volunteers[i]["name"] = st.text_input(
                    "Name", value=v["name"], key=f"vname_{i}", placeholder="e.g. Sarah"
                )
                st.session_state.volunteers[i]["address"] = st.text_input(
                    "Home address", value=v["address"], key=f"vaddr_{i}",
                    placeholder="e.g. 123 Main St, Baltimore, MD"
                )
                if len(st.session_state.volunteers) > 1:
                    if st.button("Remove", key=f"vrem_{i}"):
                        st.session_state.volunteers.pop(i)
                        st.rerun()

        if st.button("＋ Add Volunteer"):
            st.session_state.volunteers.append({"name": "", "address": ""})
            st.rerun()

    with col2:
        st.subheader("📦 Delivery Addresses")
        st.caption("Paste one address per line, or add individually.")

        bulk = st.text_area(
            "Bulk import (one address per line)",
            placeholder="123 Oak St, Baltimore, MD\n456 Elm Ave, Towson, MD\n...",
            height=120,
        )
        if st.button("Import addresses"):
            lines = [l.strip() for l in bulk.splitlines() if l.strip()]
            if "deliveries" not in st.session_state:
                st.session_state.deliveries = []
            for l in lines:
                st.session_state.deliveries.append({"address": l})
            st.rerun()

        if "deliveries" not in st.session_state:
            st.session_state.deliveries = [{"address": ""}]

        for i, d in enumerate(st.session_state.deliveries):
            c1, c2 = st.columns([5, 1])
            with c1:
                st.session_state.deliveries[i]["address"] = st.text_input(
                    f"Stop {i+1}", value=d["address"], key=f"daddr_{i}",
                    placeholder="Delivery address", label_visibility="collapsed"
                )
            with c2:
                if st.button("✕", key=f"drem_{i}") and len(st.session_state.deliveries) > 1:
                    st.session_state.deliveries.pop(i)
                    st.rerun()

        if st.button("＋ Add address"):
            st.session_state.deliveries.append({"address": ""})
            st.rerun()

    st.divider()

    if st.button("🚀 Optimize Routes", type="primary", use_container_width=True):
        vols = [v for v in st.session_state.volunteers if v["name"] and v["address"]]
        dels = [d for d in st.session_state.deliveries if d["address"]]

        if not vols:
            st.error("Please enter at least one volunteer with a name and address.")
        elif not dels:
            st.error("Please enter at least one delivery address.")
        else:
            with st.spinner("Geocoding addresses... (this may take a moment)"):
                # Geocode volunteers
                vol_results = []
                for v in vols:
                    lat, lng = geocode_address(v["address"])
                    if lat is None:
                        st.error(f"Could not geocode volunteer address: {v['address']}")
                        st.stop()
                    vol_results.append({**v, "lat": lat, "lng": lng})

                # Geocode deliveries
                del_results = []
                for d in dels:
                    lat, lng = geocode_address(d["address"])
                    if lat is None:
                        st.warning(f"Skipping unresolved address: {d['address']}")
                        continue
                    del_results.append({**d, "lat": lat, "lng": lng})

            if not del_results:
                st.error("No delivery addresses could be geocoded.")
                st.stop()

            # Cluster deliveries
            vol_coords = [(v["lat"], v["lng"]) for v in vol_results]
            clusters = {i: [] for i in range(len(vol_results))}
            for d in del_results:
                idx = assign_to_volunteer((d["lat"], d["lng"]), vol_coords)
                clusters[idx].append(d)

            # Solve TSP per cluster
            routes = []
            for vi, vol in enumerate(vol_results):
                stops = clusters[vi]
                if not stops:
                    continue
                coords = [(s["lat"], s["lng"]) for s in stops]
                order = solve_tsp(coords)
                ordered = [stops[o] for o in order]
                dist = total_distance(
                    [(vol["lat"], vol["lng"])] + coords,
                    [0] + [o + 1 for o in order]
                )
                routes.append({
                    "volunteer": vol,
                    "stops": ordered,
                    "distance_km": dist,
                    "color": COLORS[vi % len(COLORS)],
                    "hex": HEX_COLORS[vi % len(HEX_COLORS)],
                })

            st.session_state.routes = routes
            st.success(f"✅ Optimized {len(del_results)} deliveries across {len(routes)} volunteers!")
            st.balloons()

with tab_map:
    if "routes" not in st.session_state or not st.session_state.routes:
        st.info("Run the optimizer first on the Input tab.")
    else:
        routes = st.session_state.routes
        # Center map
        all_lats = [r["volunteer"]["lat"] for r in routes] + [s["lat"] for r in routes for s in r["stops"]]
        all_lngs = [r["volunteer"]["lng"] for r in routes] + [s["lng"] for r in routes for s in r["stops"]]
        center = (sum(all_lats)/len(all_lats), sum(all_lngs)/len(all_lngs))

        m = folium.Map(location=center, zoom_start=12, tiles="CartoDB positron")

        for r in routes:
            vol = r["volunteer"]
            color = r["color"]
            hex_c = r["hex"]

            # Volunteer marker
            folium.Marker(
                location=[vol["lat"], vol["lng"]],
                popup=folium.Popup(f"<b>🏠 {vol['name']}</b><br>{vol['address']}", max_width=220),
                tooltip=f"🏠 {vol['name']}",
                icon=folium.Icon(color=color, icon="home", prefix="fa"),
            ).add_to(m)

            # Route line (home → stops → home)
            line_coords = (
                [[vol["lat"], vol["lng"]]]
                + [[s["lat"], s["lng"]] for s in r["stops"]]
                + [[vol["lat"], vol["lng"]]]
            )
            folium.PolyLine(
                line_coords, color=hex_c, weight=3, opacity=0.7, dash_array="6 4",
                tooltip=f"{vol['name']}'s route ({r['distance_km']} km)"
            ).add_to(m)

            # Delivery markers
            for i, stop in enumerate(r["stops"]):
                folium.Marker(
                    location=[stop["lat"], stop["lng"]],
                    popup=folium.Popup(f"<b>Stop {i+1}</b><br>{stop['address']}", max_width=220),
                    tooltip=f"Stop {i+1} → {vol['name']}",
                    icon=folium.DivIcon(
                        html=f"""<div style="background:white;color:{hex_c};border:2px solid {hex_c};
                            border-radius:50%;width:26px;height:26px;display:flex;align-items:center;
                            justify-content:center;font-weight:bold;font-size:12px;
                            box-shadow:0 2px 4px rgba(0,0,0,0.25)">{i+1}</div>""",
                        icon_size=(26, 26), icon_anchor=(13, 13)
                    ),
                ).add_to(m)

        # Legend
        legend_html = "<div style='position:fixed;bottom:30px;left:30px;z-index:1000;background:white;padding:12px 16px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.15);font-family:sans-serif;font-size:13px'>"
        legend_html += "<b>Volunteers</b><br>"
        for r in routes:
            legend_html += f"<span style='color:{r['hex']}'>●</span> {r['volunteer']['name']} &nbsp;({len(r['stops'])} stops, {r['distance_km']} km)<br>"
        legend_html += "</div>"
        m.get_root().html.add_child(folium.Element(legend_html))

        st_folium(m, use_container_width=True, height=580)

with tab_routes:
    if "routes" not in st.session_state or not st.session_state.routes:
        st.info("Run the optimizer first on the Input tab.")
    else:
        routes = st.session_state.routes

        # Summary table
        summary = pd.DataFrame([{
            "Volunteer": r["volunteer"]["name"],
            "Deliveries": len(r["stops"]),
            "Est. Distance (km)": r["distance_km"],
        } for r in routes])
        st.dataframe(summary, use_container_width=True, hide_index=True)
        st.divider()

        for r in routes:
            vol = r["volunteer"]
            with st.expander(f"📍 {vol['name']} — {len(r['stops'])} stops ({r['distance_km']} km)", expanded=True):
                steps = [{"#": "🏠", "Address": vol["address"], "Note": "Start (home)"}]
                for i, s in enumerate(r["stops"]):
                    steps.append({"#": i + 1, "Address": s["address"], "Note": ""})
                steps.append({"#": "🏠", "Address": vol["address"], "Note": "Return home"})
                st.table(pd.DataFrame(steps))
