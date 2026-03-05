import streamlit as st
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import pandas as pd
import math
import requests
import urllib.parse
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
KM_TO_MILES = 0.621371

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
    try:
        coords = ";".join(f"{lng},{lat}" for lat, lng in points)
        url = f"{OSRM_BASE}/table/v1/driving/{coords}?annotations=distance"
        r = requests.get(url, timeout=15)
        data = r.json()
        if data["code"] == "Ok":
            return [[d / 1000 for d in row] for row in data["distances"]]
    except:
        pass
    return [[haversine(points[i], points[j]) for j in range(len(points))] for i in range(len(points))]

@st.cache_data(show_spinner=False)
def osrm_route_geometry(waypoints):
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

# ── Helpers ────────────────────────────────────────────────────────────────────
def haversine(a, b):
    R = 6371
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(h))

def km_to_miles(km):
    return round(km * KM_TO_MILES, 2)

def google_maps_url(address):
    return f"https://www.google.com/maps/search/?api=1&query={address.replace(' ', '+')}"

def google_maps_directions(origin, destination):
    return f"https://www.google.com/maps/dir/{origin.replace(' ', '+')}/{destination.replace(' ', '+')}"

def mailto_link(to_email, subject, body):
    params = urllib.parse.urlencode({"subject": subject, "body": body})
    return f"mailto:{to_email}?{params}"

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

def route_cost(full_matrix, route, home_idx):
    cost = full_matrix[home_idx][route[0]]
    for i in range(len(route) - 1):
        cost += full_matrix[route[i]][route[i+1]]
    cost += full_matrix[route[-1]][home_idx]
    return cost

def solve_tsp_from_home(full_matrix, home_idx, stop_indices):
    if not stop_indices:
        return [], 0.0
    n = len(stop_indices)
    sub = [[full_matrix[stop_indices[i]][stop_indices[j]] for j in range(n)] for i in range(n)]
    best_route, best_cost = None, float("inf")
    for start in range(n):
        route = nearest_neighbor(sub, start)
        route = two_opt(sub, route)
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

# ── Email generator ────────────────────────────────────────────────────────────
def generate_email_body(route):
    vol = route["volunteer"]
    stops = route["stops"]
    miles = km_to_miles(route["distance_km"])
    lines = []
    lines.append(f"Hi {vol['name']},")
    lines.append(f"\nThank you for volunteering to deliver yard signs for Conway for Congress!")
    lines.append(f"\nYou have {len(stops)} stop{'s' if len(stops) > 1 else ''} assigned to you, covering approximately {miles} miles. Please start from your home address and follow the route below:\n")
    for i, s in enumerate(stops):
        prev = vol["address"] if i == 0 else stops[i-1]["address"]
        note = f" -- Note: {s['note']}" if s.get("note") else ""
        lines.append(f"  Stop {i+1}: {s['address']}{note}")
        lines.append(f"  Directions: {google_maps_directions(prev, s['address'])}\n")
    lines.append(f"Return home to: {vol['address']}")
    lines.append(f"\nTotal estimated driving: {miles} miles")
    lines.append(f"\nThank you again for your support! Please reach out if you have any questions.")
    lines.append(f"\nBest,\nConway for Congress Team")
    return "\n".join(lines)

# ── Load saved data ────────────────────────────────────────────────────────────
if "loaded" not in st.session_state:
    saved_roster    = load_data("volunteer_roster")
    saved_dels      = load_data("deliveries")
    saved_completed = load_data("completed")
    # Roster = full list of all volunteers ever added
    st.session_state.volunteer_roster = saved_roster if saved_roster else []
    st.session_state.deliveries  = saved_dels if saved_dels else [{"address": "", "note": ""}]
    st.session_state.completed   = {c["key"]: c for c in saved_completed} if saved_completed else {}
    # availability is a set of volunteer names checked as available this run
    st.session_state.availability = {v["name"] for v in st.session_state.volunteer_roster if v.get("available", False)}
    st.session_state.loaded = True

# ── UI ─────────────────────────────────────────────────────────────────────────
st.title("🗺️ Conway for Congress — Yard Sign Route Optimizer")
st.caption("Clusters deliveries by driving distance, then finds the most efficient road-based route per volunteer.")

tab_roster, tab_input, tab_map, tab_routes, tab_emails = st.tabs([
    "👥 Volunteers", "📋 Delivery Run", "🗺️ Map", "📍 Routes", "📧 Emails"
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — VOLUNTEER ROSTER (permanent list)
# ══════════════════════════════════════════════════════════════════════════════
with tab_roster:
    st.subheader("👥 Volunteer Roster")
    st.caption("Add all your volunteers here once. For each delivery run, mark who is available on the Delivery Run tab.")

    roster = st.session_state.volunteer_roster

    # Existing volunteers
    for i, v in enumerate(roster):
        with st.container(border=True):
            c1, c2 = st.columns([8, 1])
            with c1:
                color_dot = f'<span style="color:{HEX_COLORS[i % len(HEX_COLORS)]};font-size:18px;">&#9679;</span>'
                st.markdown(f"{color_dot} **{v['name'] if v['name'] else f'Volunteer {i+1}'}**", unsafe_allow_html=True)
                col1, col2, col3 = st.columns(3)
                with col1:
                    roster[i]["name"] = st.text_input("Name", value=v["name"], key=f"rname_{i}", placeholder="Full name")
                with col2:
                    roster[i]["email"] = st.text_input("Email", value=v.get("email", ""), key=f"remail_{i}", placeholder="email@example.com")
                with col3:
                    roster[i]["address"] = st.text_input("Home address", value=v.get("address", ""), key=f"raddr_{i}", placeholder="123 Main St, Baltimore, MD")
            with c2:
                st.write("")
                st.write("")
                if st.button("Remove", key=f"rrem_{i}"):
                    st.session_state.volunteer_roster.pop(i)
                    save_data("volunteer_roster", st.session_state.volunteer_roster)
                    st.rerun()

    col_add, col_save = st.columns(2)
    with col_add:
        if st.button("+ Add Volunteer", use_container_width=True):
            st.session_state.volunteer_roster.append({"name": "", "email": "", "address": ""})
            st.rerun()
    with col_save:
        if st.button("💾 Save Roster", type="primary", use_container_width=True):
            save_data("volunteer_roster", st.session_state.volunteer_roster)
            st.success("Roster saved!")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — DELIVERY RUN (mark availability + delivery addresses)
# ══════════════════════════════════════════════════════════════════════════════
with tab_input:
    st.subheader("📋 Delivery Run Setup")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Who is available today?**")
        st.caption("Check the volunteers who can deliver signs in this run.")

        roster = st.session_state.volunteer_roster
        if not roster:
            st.info("No volunteers yet — add them in the Volunteers tab first.")
        else:
            for i, v in enumerate(roster):
                if not v.get("name"):
                    continue
                color_dot = f'<span style="color:{HEX_COLORS[i % len(HEX_COLORS)]};font-size:16px;">&#9679;</span>'
                is_available = v["name"] in st.session_state.availability
                col_dot, col_check = st.columns([1, 9])
                with col_dot:
                    st.markdown(color_dot, unsafe_allow_html=True)
                with col_check:
                    checked = st.checkbox(
                        f"**{v['name']}** — {v.get('address', 'no address')}",
                        value=is_available,
                        key=f"avail_{i}"
                    )
                    if checked:
                        st.session_state.availability.add(v["name"])
                    else:
                        st.session_state.availability.discard(v["name"])

    with col2:
        st.markdown("**Delivery Addresses**")
        st.caption("Paste supporter addresses who need a sign dropped off.")

        bulk = st.text_area(
            "Bulk import (one address per line)",
            placeholder="123 Oak St, Baltimore, MD\n456 Elm Ave, Towson, MD\n...",
            height=100,
        )
        if st.button("Import addresses"):
            lines = [l.strip() for l in bulk.splitlines() if l.strip()]
            for l in lines:
                st.session_state.deliveries.append({"address": l, "note": ""})
            save_data("deliveries", st.session_state.deliveries)
            st.rerun()

        for i, d in enumerate(st.session_state.deliveries):
            with st.container(border=True):
                c1, c2 = st.columns([5, 1])
                with c1:
                    st.session_state.deliveries[i]["address"] = st.text_input(
                        f"Address {i+1}", value=d["address"], key=f"daddr_{i}",
                        placeholder="Delivery address", label_visibility="collapsed"
                    )
                with c2:
                    if st.button("X", key=f"drem_{i}") and len(st.session_state.deliveries) > 1:
                        st.session_state.deliveries.pop(i)
                        save_data("deliveries", st.session_state.deliveries)
                        st.rerun()
                st.session_state.deliveries[i]["note"] = st.text_input(
                    "Note", value=d.get("note", ""), key=f"dnote_{i}",
                    placeholder="e.g. leave at side door (optional)"
                )
        if st.button("+ Add address"):
            st.session_state.deliveries.append({"address": "", "note": ""})
            st.rerun()

    st.divider()
    col_save, col_clear, col_optimize = st.columns(3)

    with col_save:
        if st.button("💾 Save Deliveries", use_container_width=True):
            save_data("deliveries", st.session_state.deliveries)
            st.success("Saved!")

    with col_clear:
        if st.button("🗑️ Clear Deliveries", use_container_width=True):
            st.session_state.deliveries = [{"address": "", "note": ""}]
            st.session_state.completed = {}
            st.session_state.routes = []
            st.session_state.availability = set()
            save_data("deliveries", st.session_state.deliveries)
            save_data("completed", [])
            st.rerun()

    with col_optimize:
        if st.button("🚀 Optimize Routes", type="primary", use_container_width=True):
            active_vols = [v for v in st.session_state.volunteer_roster
                          if v.get("name") and v.get("address") and v["name"] in st.session_state.availability]
            dels = [d for d in st.session_state.deliveries if d["address"]]

            if not active_vols:
                st.error("Please check at least one available volunteer above.")
            elif not dels:
                st.error("Please enter at least one delivery address.")
            else:
                save_data("deliveries", st.session_state.deliveries)

                with st.spinner("Geocoding addresses..."):
                    vol_results = []
                    for v in active_vols:
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

                with st.spinner("Building driving distance matrix..."):
                    all_points = [(v["lat"], v["lng"]) for v in vol_results] + \
                                 [(d["lat"], d["lng"]) for d in del_results]
                    n_vols = len(vol_results)
                    full_matrix = osrm_matrix(all_points)

                with st.spinner("Clustering and optimizing routes..."):
                    clusters = {i: [] for i in range(n_vols)}
                    for di in range(len(del_results)):
                        d_idx = n_vols + di
                        best_vol = min(range(n_vols), key=lambda vi: full_matrix[vi][d_idx])
                        clusters[best_vol].append(d_idx)

                    routes = []
                    for vi, vol in enumerate(vol_results):
                        stop_indices = clusters[vi]
                        if not stop_indices:
                            continue
                        ordered_indices, dist_km = solve_tsp_from_home(full_matrix, vi, stop_indices)
                        ordered_stops = [del_results[idx - n_vols] for idx in ordered_indices]
                        waypoints = (
                            [(vol["lat"], vol["lng"])]
                            + [(s["lat"], s["lng"]) for s in ordered_stops]
                            + [(vol["lat"], vol["lng"])]
                        )
                        road_geometry = osrm_route_geometry(waypoints)
                        routes.append({
                            "volunteer": vol,
                            "stops": ordered_stops,
                            "distance_km": dist_km,
                            "distance_miles": km_to_miles(dist_km),
                            "road_geometry": road_geometry,
                            "color": COLORS[vi % len(COLORS)],
                            "hex": HEX_COLORS[vi % len(HEX_COLORS)],
                        })

                st.session_state.routes = routes
                st.success(f"Optimized {len(del_results)} deliveries across {len(routes)} volunteers!")
                st.balloons()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — MAP
# ══════════════════════════════════════════════════════════════════════════════
with tab_map:
    if "routes" not in st.session_state or not st.session_state.routes:
        st.info("Run the optimizer first on the Delivery Run tab.")
    else:
        routes = st.session_state.routes
        completed = st.session_state.completed

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
                    f"<b>Home: {vol['name']}</b><br>{vol['address']}<br>"
                    f"<a href='{google_maps_url(vol['address'])}' target='_blank'>Open in Google Maps</a>",
                    max_width=250
                ),
                tooltip=f"Home: {vol['name']}",
                icon=folium.Icon(color=color, icon="home", prefix="fa"),
            ).add_to(m)

            if r.get("road_geometry"):
                folium.PolyLine(
                    r["road_geometry"], color=hex_c, weight=4, opacity=0.8,
                    tooltip=f"{vol['name']}: {r['distance_miles']} mi"
                ).add_to(m)

            for i, stop in enumerate(r["stops"]):
                prev = vol["address"] if i == 0 else r["stops"][i-1]["address"]
                key = vol["name"] + "_" + str(i)
                is_done = key in completed
                note_html = f"<br><i>Note: {stop['note']}</i>" if stop.get("note") else ""

                if is_done:
                    folium.Marker(
                        location=[stop["lat"], stop["lng"]],
                        popup=folium.Popup(
                            f"<b>Delivered!</b><br>{stop['address']}{note_html}<br>"
                            f"<i>Delivered by {vol['name']}</i>",
                            max_width=250
                        ),
                        tooltip=f"Delivered - Stop {i+1} ({vol['name']})",
                        icon=folium.Icon(color="green", icon="check", prefix="fa"),
                    ).add_to(m)
                else:
                    folium.Marker(
                        location=[stop["lat"], stop["lng"]],
                        popup=folium.Popup(
                            f"<b>Stop {i+1} - {vol['name']}</b><br>{stop['address']}{note_html}<br>"
                            f"<a href='{google_maps_directions(prev, stop['address'])}' target='_blank'>Get Directions</a>",
                            max_width=250
                        ),
                        tooltip=f"Stop {i+1} - {vol['name']}",
                        icon=folium.DivIcon(
                            html=f"""<div style="background:white;color:{hex_c};border:2px solid {hex_c};
                                border-radius:50%;width:26px;height:26px;display:flex;align-items:center;
                                justify-content:center;font-weight:bold;font-size:12px;
                                box-shadow:0 2px 4px rgba(0,0,0,0.25)">{i+1}</div>""",
                            icon_size=(26, 26), icon_anchor=(13, 13)
                        ),
                    ).add_to(m)

        # ── Simple legend ──
        col_a, col_b, col_rest = st.columns([1, 1, 6])
        col_a.markdown('<span style="color:#27ae60;font-size:20px;">&#9679;</span> Sign placed', unsafe_allow_html=True)
        col_b.markdown('<span style="color:#aaa;font-size:20px;">&#9679;</span> Pending', unsafe_allow_html=True)
        st_folium(m, use_container_width=True, height=580)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — ROUTES
# ══════════════════════════════════════════════════════════════════════════════
with tab_routes:
    if "routes" not in st.session_state or not st.session_state.routes:
        st.info("Run the optimizer first on the Delivery Run tab.")
    else:
        routes = st.session_state.routes
        completed = st.session_state.completed

        summary_rows = []
        for r in routes:
            vol_name = r["volunteer"]["name"]
            done = sum(1 for i in range(len(r["stops"])) if vol_name + "_" + str(i) in completed)
            summary_rows.append({
                "Volunteer": vol_name,
                "Email": r["volunteer"].get("email", ""),
                "Deliveries": len(r["stops"]),
            })
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)
        st.divider()

        for r in routes:
            vol = r["volunteer"]
            vol_name = vol["name"]
            done_count = sum(1 for i in range(len(r["stops"])) if vol_name + "_" + str(i) in completed)
            with st.expander(vol_name + " — " + str(len(r["stops"])) + " stops", expanded=True):
                st.markdown(f"**Start:** {vol['address']}")
                st.divider()
                for i, s in enumerate(r["stops"]):
                    prev = vol["address"] if i == 0 else r["stops"][i-1]["address"]
                    key = vol_name + "_" + str(i)
                    is_done = key in completed
                    col_check, col_info = st.columns([1, 9])
                    with col_check:
                        checked = st.checkbox("", value=is_done, key=f"chk_{key}")
                        if checked and not is_done:
                            st.session_state.completed[key] = {
                                "key": key,
                                "address": s["address"],
                                "lat": s["lat"],
                                "lng": s["lng"],
                                "volunteer": vol_name,
                                "stop_num": i + 1,
                            }
                            save_data("completed", list(st.session_state.completed.values()))
                            st.rerun()
                        elif not checked and is_done:
                            del st.session_state.completed[key]
                            save_data("completed", list(st.session_state.completed.values()))
                            st.rerun()
                    with col_info:
                        note_text = f" — *{s['note']}*" if s.get("note") else ""
                        if is_done:
                            st.markdown(f"~~**Stop {i+1}:** {s['address']}~~ ✅{note_text}")
                        else:
                            st.markdown(f"**Stop {i+1}:** {s['address']}{note_text}")
                        st.markdown(f"[Get Directions]({google_maps_directions(prev, s['address'])})")
                st.divider()
                st.markdown(f"**Return home:** {vol['address']}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — EMAILS
# ══════════════════════════════════════════════════════════════════════════════
with tab_emails:
    if "routes" not in st.session_state or not st.session_state.routes:
        st.info("Run the optimizer first on the Delivery Run tab.")
    else:
        st.subheader("📧 Volunteer Route Emails")
        st.caption("Click 'Open in Mail App' to send directly, or copy the text below.")

        for r in st.session_state.routes:
            vol = r["volunteer"]
            email_body = generate_email_body(r)
            subject = "Conway for Congress - Your Yard Sign Delivery Route"
            vol_email = vol.get("email", "")

            with st.expander("Email for " + vol["name"] + " — " + (vol_email if vol_email else "no email on file"), expanded=True):
                if vol_email:
                    mailto = mailto_link(vol_email, subject, email_body)
                    st.markdown(
                        f'<a href="{mailto}" style="display:inline-block;padding:10px 20px;'
                        f'background:#2563eb;color:white;border-radius:8px;text-decoration:none;'
                        f'font-weight:600;font-size:14px;">Open in Mail App</a>',
                        unsafe_allow_html=True
                    )
                    st.caption(f"Sends to: {vol_email}")
                else:
                    st.warning("No email address on file — add it in the Volunteers tab.")
                st.text_area(
                    "Or copy manually:",
                    value=email_body,
                    height=300,
                    key=f"email_{vol['name']}"
                )
