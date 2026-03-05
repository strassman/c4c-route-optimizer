import streamlit as st
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import pandas as pd
import math
import requests
from supabase import create_client

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="C4C Route Optimizer",
    page_icon="🗺️",
    layout="wide",
)

COLORS = ["red", "blue", "green", "orange", "purple", "darkred", "cadetblue", "darkgreen"]
HEX_COLORS = ["#e74c3c","#3498db","#2ecc71","#f39c12","#9b59b6","#c0392b","#5f9ea0","#27ae60"]
OSRM_BASE = "https://router.project-osrm.org"

# ── Supabase ───────────────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

def load_data(key):
    try:
        sb = get_supabase()
        res = sb.table("campaign_data").select("data").eq("id", key).execute()
        return res.data[0]["data"] if res.data else []
    except:
        return []

def save_data(key, value):
    try:
        sb = get_supabase()
        sb.table("campaign_data").upsert({"id": key, "data": value, "updated_at": "now()"}).execute()
    except Exception as e:
        st.error(f"Failed to save: {e}")

# ── OSRM ───────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def osrm_matrix(points):
    """Full NxN driving distance matrix in km via OSRM table API."""
    try:
        coords = ";".join(f"{lng},{lat}" for lat, lng in points)
        url = f"{OSRM_BASE}/table/v1/driving/{coords}?annotations=distance"
        r = requests.get(url, timeout=15)
        data = r.json()
        if data["code"] == "Ok":
            return [[d / 1000 for d in row] for row in data["distances"]]
    except:
        pass
    # Fallback to haversine
    return [[haversine(points[i], points[j]) for j in range(len(points))] for i in range(len(points))]

@st.cache_data(show_spinner=False)
def osrm_route_geometry(waypoints):
    """Actual road polyline for a list of (lat, lng) waypoints."""
    try:
        coords = ";".join(f"{lng},{lat}" for lat, lng in waypoints)
        url = f"{OSRM_BASE}/route/v1/driving/{coords}?overview=full&geometries=geojson"
        r = requests.get(url, timeout=15)
        data = r.json()
        if data["code"] == "Ok":
            return [[pt[1], pt[0]] for pt in data["routes"][0]["geometry"]["coordinates"]]
    except:
        pass
    return [[lat, lng] for lat, lng in waypoints]

# ── Haversine fallback ─────────────────────────────────────────────────────────
def haversine(a, b):
    R = 6371
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(h))

# ── TSP ────────────────────────────────────────────────────────────────────────
def nearest_neighbor(dist_matrix, start=0):
    n = len(dist_matrix)
    if n <= 1:
        return list(range(n))
    visited = [False] * n
    route = [start]
    visited[start] = True
    for _ in range(n - 1):
        last = route[-1]
        best, best_d = -1, float("inf")
        for j in range(n):
            if not visited[j] and dist_matrix[last][j] < best_d:
                best_d = dist_matrix[last][j]
                best = j
        route.append(best)
        visited[best] = True
    return route

def two_opt(dist_matrix, route):
    improved = True
    while improved:
        improved = False
        n = len(route)
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                a, b = route[i-1], route[i]
                c, d = route[j], route[(j+1) % n]
                if dist_matrix[a][b] + dist_matrix[c][d] > dist_matrix[a][c] + dist_matrix[b][d] + 1e-10:
                    route[i:j+1] = route[i:j+1][::-1]
                    improved = True
    return route

def route_cost(dist_matrix, route, home_idx):
    """Total cost: home → route[0] → ... → route[-1] → home."""
    cost = dist_matrix[home_idx][route[0]]
    for i in range(len(route) - 1):
        cost += dist_matrix[route[i]][route[i+1]]
    cost += dist_matrix[route[-1]][home_idx]
    return cost

def solve_tsp_from_home(full_matrix, home_idx, stop_indices):
    """
    Solve TSP for stop_indices with home_idx as fixed start/end.
    Tries all starting stops and picks the best overall route.
    """
    if not stop_indices:
        return [], 0.0

    # Build sub-matrix for stops only
    n = len(stop_indices)
    sub = [[full_matrix[stop_indices[i]][stop_indices[j]] for j in range(n)] for i in range(n)]

    best_route, best_cost = None, float("inf")

    # Try nearest-neighbor from every possible starting stop
    for start in range(n):
        route = nearest_neighbor(sub, start)
        route = two_opt(sub, route)
        # Map back to full matrix indices for cost calculation
        full_route = [stop_indices[r] for r in route]
        cost = route_cost(full_matrix, full_route, home_idx)
        if cost < best_cost:
            best_cost = cost
            best_route = full_route

    return best_route, round(best_cost, 2)

# ── Geocoding ──────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def geocode_address(address: str):
    geolocator = Nominatim(user_agent="c4c_route_optimizer")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
    loc = geocode(address)
    if loc is None:
        return None, None
    return loc.latitude, loc.longitude

def google_maps_url(address):
    return f"https://www.google.com/maps/search/?api=1&query={address.replace(' ', '+')}"

def google_maps_directions(origin, destination):
    return f"https://www.google.com/maps/dir/{origin.replace(' ', '+')}/{destination.replace(' ', '+')}"

# ── Load saved data ────────────────────────────────────────────────────────────
if "loaded" not in st.session_state:
    saved_vols = load_data("volunteers")
    saved_dels = load_data("deliveries")
    st.session_state.volunteers = saved_vols if saved_vols else [{"name": "", "address": ""}]
    st.session_state.deliveries = saved_dels if saved_dels else [{"address": ""}]
    st.session_state.loaded = True

# ── UI ─────────────────────────────────────────────────────────────────────────
st.title("🗺️ Conway for Congress — Yard Sign Route Optimizer")
st.caption("Clusters deliveries by driving distance, then finds the most efficient road-based route per volunteer.")

tab_input, tab_map, tab_routes = st.tabs(["📋 Input", "🗺️ Map", "📍 Routes"])

with tab_input:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("👤 Volunteers")
        st.caption("Add each volunteer's name and home address.")

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
                        save_data("volunteers", st.session_state.volunteers)
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
            for l in lines:
                st.session_state.deliveries.append({"address": l})
            save_data("deliveries", st.session_state.deliveries)
            st.rerun()

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
                    save_data("deliveries", st.session_state.deliveries)
                    st.rerun()

        if st.button("＋ Add address"):
            st.session_state.deliveries.append({"address": ""})
            st.rerun()

    st.divider()

    col_save, col_optimize = st.columns(2)

    with col_save:
        if st.button("💾 Save Addresses", use_container_width=True):
            save_data("volunteers", st.session_state.volunteers)
            save_data("deliveries", st.session_state.deliveries)
            st.success("Addresses saved!")

    with col_optimize:
        if st.button("🚀 Optimize Routes", type="primary", use_container_width=True):
            vols = [v for v in st.session_state.volunteers if v["name"] and v["address"]]
            dels = [d for d in st.session_state.deliveries if d["address"]]

            if not vols:
                st.error("Please enter at least one volunteer with a name and address.")
            elif not dels:
                st.error("Please enter at least one delivery address.")
            else:
                save_data("volunteers", st.session_state.volunteers)
                save_data("deliveries", st.session_state.deliveries)

                with st.spinner("Geocoding addresses..."):
                    vol_results = []
                    for v in vols:
                        lat, lng = geocode_address(v["address"])
                        if lat is None:
                            st.error(f"Could not geocode: {v['address']}")
                            st.stop()
                        vol_results.append({**v, "lat": lat, "lng": lng})

                    del_results = []
                    for d in dels:
                        lat, lng = geocode_address(d["address"])
                        if lat is None:
                            st.warning(f"Skipping: {d['address']}")
                            continue
                        del_results.append({**d, "lat": lat, "lng": lng})

                if not del_results:
                    st.error("No delivery addresses could be geocoded.")
                    st.stop()

                with st.spinner("Building driving distance matrix via OSRM..."):
                    # All points: volunteers first, then deliveries
                    all_points = [(v["lat"], v["lng"]) for v in vol_results] + \
                                 [(d["lat"], d["lng"]) for d in del_results]
                    n_vols = len(vol_results)
                    full_matrix = osrm_matrix(all_points)

                with st.spinner("Clustering by driving distance & optimizing routes..."):
                    # Assign each delivery to the volunteer with shortest driving distance
                    clusters = {i: [] for i in range(n_vols)}
                    for di, d in enumerate(del_results):
                        d_idx = n_vols + di
                        best_vol = min(range(n_vols), key=lambda vi: full_matrix[vi][d_idx])
                        clusters[best_vol].append(d_idx)

                    routes = []
                    for vi, vol in enumerate(vol_results):
                        stop_indices = clusters[vi]
                        if not stop_indices:
                            continue

                        ordered_indices, dist = solve_tsp_from_home(full_matrix, vi, stop_indices)
                        ordered_stops = [del_results[idx - n_vols] for idx in ordered_indices]

                        # Road geometry
                        waypoints = (
                            [(vol["lat"], vol["lng"])]
                            + [(s["lat"], s["lng"]) for s in ordered_stops]
                            + [(vol["lat"], vol["lng"])]
                        )
                        road_geometry = osrm_route_geometry(waypoints)

                        routes.append({
                            "volunteer": vol,
                            "stops": ordered_stops,
                            "distance_km": dist,
                            "road_geometry": road_geometry,
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
        all_lats = [r["volunteer"]["lat"] for r in routes] + [s["lat"] for r in routes for s in r["stops"]]
        all_lngs = [r["volunteer"]["lng"] for r in routes] + [s["lng"] for r in routes for s in r["stops"]]
        center = (sum(all_lats)/len(all_lats), sum(all_lngs)/len(all_lngs))

        m = folium.Map(location=center, zoom_start=12, tiles="CartoDB positron")

        for r in routes:
            vol = r["volunteer"]
            hex_c = r["hex"]
            color = r["color"]

            folium.Marker(
                location=[vol["lat"], vol["lng"]],
                popup=folium.Popup(
                    f"<b>🏠 {vol['name']}</b><br>{vol['address']}<br>"
                    f"<a href='{google_maps_url(vol['address'])}' target='_blank'>Open in Google Maps</a>",
                    max_width=250
                ),
                tooltip=f"🏠 {vol['name']}",
                icon=folium.Icon(color=color, icon="home", prefix="fa"),
            ).add_to(m)

            if r.get("road_geometry"):
                folium.PolyLine(
                    r["road_geometry"], color=hex_c, weight=4, opacity=0.8,
                    tooltip=f"{vol['name']}'s route ({r['distance_km']} km driving)"
                ).add_to(m)

            for i, stop in enumerate(r["stops"]):
                prev = vol["address"] if i == 0 else r["stops"][i-1]["address"]
                folium.Marker(
                    location=[stop["lat"], stop["lng"]],
                    popup=folium.Popup(
                        f"<b>Stop {i+1}</b><br>{stop['address']}<br>"
                        f"<a href='{google_maps_directions(prev, stop['address'])}' target='_blank'>📍 Directions from previous stop</a>",
                        max_width=250
                    ),
                    tooltip=f"Stop {i+1} → {vol['name']}",
                    icon=folium.DivIcon(
                        html=f"""<div style="background:white;color:{hex_c};border:2px solid {hex_c};
                            border-radius:50%;width:26px;height:26px;display:flex;align-items:center;
                            justify-content:center;font-weight:bold;font-size:12px;
                            box-shadow:0 2px 4px rgba(0,0,0,0.25)">{i+1}</div>""",
                        icon_size=(26, 26), icon_anchor=(13, 13)
                    ),
                ).add_to(m)

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

        summary = pd.DataFrame([{
            "Volunteer": r["volunteer"]["name"],
            "Deliveries": len(r["stops"]),
            "Driving Distance (km)": r["distance_km"],
        } for r in routes])
        st.dataframe(summary, use_container_width=True, hide_index=True)
        st.divider()

        for r in routes:
            vol = r["volunteer"]
            with st.expander(f"📍 {vol['name']} — {len(r['stops'])} stops ({r['distance_km']} km)", expanded=True):
                steps = [{"#": "🏠", "Address": vol["address"], "Google Maps": google_maps_url(vol["address"]), "Note": "Start (home)"}]
                for i, s in enumerate(r["stops"]):
                    prev = vol["address"] if i == 0 else r["stops"][i-1]["address"]
                    steps.append({"#": i+1, "Address": s["address"], "Google Maps": google_maps_directions(prev, s["address"]), "Note": ""})
                steps.append({"#": "🏠", "Address": vol["address"], "Google Maps": google_maps_url(vol["address"]), "Note": "Return home"})
                st.dataframe(
                    pd.DataFrame(steps),
                    use_container_width=True,
                    hide_index=True,
                    column_config={"Google Maps": st.column_config.LinkColumn("Google Maps")}
                )
