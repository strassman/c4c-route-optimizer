import streamlit as st
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import pandas as pd
import math
import requests
import urllib.parse
import uuid
from datetime import datetime
from supabase import create_client

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="C4C Route Optimizer", page_icon="🗺️", layout="wide")

COLORS    = ["red","blue","green","orange","purple","darkred","cadetblue","darkgreen"]
HEX_COLORS= ["#e74c3c","#3498db","#2ecc71","#f39c12","#9b59b6","#c0392b","#5f9ea0","#27ae60"]
OSRM_BASE = "https://router.project-osrm.org"
KM_TO_MILES = 0.621371

# ── Supabase ───────────────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

def load_data(key):
    try:
        res = get_supabase().table("campaign_data").select("data").eq("id", key).execute()
        return res.data[0]["data"] if res.data else []
    except:
        return []

def save_data(key, value):
    try:
        get_supabase().table("campaign_data").upsert({"id": key, "data": value, "updated_at": "now()"}).execute()
    except Exception as e:
        st.error(f"Failed to save: {e}")

# ── OSRM ───────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def osrm_matrix(points):
    try:
        coords = ";".join(f"{lng},{lat}" for lat, lng in points)
        r = requests.get(f"{OSRM_BASE}/table/v1/driving/{coords}?annotations=distance", timeout=15)
        data = r.json()
        if data["code"] == "Ok":
            return [[d/1000 for d in row] for row in data["distances"]]
    except:
        pass
    return [[haversine(points[i], points[j]) for j in range(len(points))] for i in range(len(points))]

@st.cache_data(show_spinner=False)
def osrm_route_geometry(waypoints):
    try:
        coords = ";".join(f"{lng},{lat}" for lat, lng in waypoints)
        r = requests.get(f"{OSRM_BASE}/route/v1/driving/{coords}?overview=full&geometries=geojson", timeout=15)
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
    dlat, dlon = lat2-lat1, lon2-lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(h))

def km_to_miles(km): return round(km * KM_TO_MILES, 2)
def gmaps_url(address): return f"https://www.google.com/maps/search/?api=1&query={address.replace(' ','+')}"
def gmaps_dir(origin, dest): return f"https://www.google.com/maps/dir/{origin.replace(' ','+')}/{dest.replace(' ','+')}"
def mailto_link(to, subject, body):
    return f"mailto:{to}?" + urllib.parse.urlencode({"subject": subject, "body": body})

@st.cache_data(show_spinner=False)
def geocode_address(address: str):
    geolocator = Nominatim(user_agent="c4c_route_optimizer")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
    loc = geocode(address)
    return (loc.latitude, loc.longitude) if loc else (None, None)

# ── TSP ────────────────────────────────────────────────────────────────────────
def nearest_neighbor(dist_matrix, start=0):
    n = len(dist_matrix)
    if n <= 1: return list(range(n))
    visited = [False]*n; route = [start]; visited[start] = True
    for _ in range(n-1):
        last = route[-1]
        best, best_d = -1, float("inf")
        for j in range(n):
            if not visited[j] and dist_matrix[last][j] < best_d:
                best_d = dist_matrix[last][j]; best = j
        route.append(best); visited[best] = True
    return route

def two_opt(dist_matrix, route):
    improved = True
    while improved:
        improved = False; n = len(route)
        for i in range(1, n-1):
            for j in range(i+1, n):
                a,b = route[i-1],route[i]; c,d = route[j],route[(j+1)%n]
                if dist_matrix[a][b]+dist_matrix[c][d] > dist_matrix[a][c]+dist_matrix[b][d]+1e-10:
                    route[i:j+1] = route[i:j+1][::-1]; improved = True
    return route

def route_cost(full_matrix, route, home_idx):
    cost = full_matrix[home_idx][route[0]]
    for i in range(len(route)-1): cost += full_matrix[route[i]][route[i+1]]
    cost += full_matrix[route[-1]][home_idx]
    return cost

def solve_tsp_from_home(full_matrix, home_idx, stop_indices):
    if not stop_indices: return [], 0.0
    n = len(stop_indices)
    sub = [[full_matrix[stop_indices[i]][stop_indices[j]] for j in range(n)] for i in range(n)]
    best_route, best_cost = None, float("inf")
    for start in range(n):
        route = two_opt(sub, nearest_neighbor(sub, start))
        full_route = [stop_indices[r] for r in route]
        cost = route_cost(full_matrix, full_route, home_idx)
        if cost < best_cost: best_cost = cost; best_route = full_route
    return best_route, round(best_cost, 2)

# ── Email ──────────────────────────────────────────────────────────────────────
def generate_email_body(route):
    vol = route["volunteer"]; stops = route["stops"]; miles = km_to_miles(route["distance_km"])
    lines = [f"Hi {vol['name']},",
             f"\nThank you for volunteering to deliver yard signs for Conway for Congress!",
             f"\nYou have {len(stops)} stop{'s' if len(stops)>1 else ''} assigned, covering approximately {miles} miles:\n"]
    for i, s in enumerate(stops):
        prev = vol["address"] if i == 0 else stops[i-1]["address"]
        note = f" -- Note: {s['note']}" if s.get("note") else ""
        lines += [f"  Stop {i+1}: {s['address']}{note}", f"  Directions: {gmaps_dir(prev, s['address'])}\n"]
    lines += [f"Return home: {vol['address']}", f"\nTotal: ~{miles} miles",
              f"\nThank you!\nConway for Congress Team"]
    return "\n".join(lines)

# ── Load data ──────────────────────────────────────────────────────────────────
if "loaded" not in st.session_state:
    st.session_state.volunteer_roster  = load_data("volunteer_roster") or []
    st.session_state.master_addresses  = load_data("master_addresses") or []
    st.session_state.run_address_ids   = load_data("run_address_ids") or []   # list of IDs in current run
    st.session_state.completed         = {c["key"]: c for c in (load_data("completed") or [])}
    st.session_state.availability      = set()
    st.session_state.loaded = True

def get_master_by_id(aid):
    return next((a for a in st.session_state.master_addresses if a["id"] == aid), None)

def upsert_master(addr_dict):
    """Add or update an address in the master list by id."""
    existing = next((i for i,a in enumerate(st.session_state.master_addresses) if a["id"] == addr_dict["id"]), None)
    if existing is not None:
        st.session_state.master_addresses[existing] = addr_dict
    else:
        st.session_state.master_addresses.append(addr_dict)
    save_data("master_addresses", st.session_state.master_addresses)

# ── UI ─────────────────────────────────────────────────────────────────────────
st.title("🗺️ Conway for Congress — Yard Sign Route Optimizer")
st.caption("Manage volunteers, plan delivery runs, and track every sign on the map.")

tab_roster, tab_addresses, tab_run, tab_map, tab_routes, tab_emails = st.tabs([
    "👥 Volunteers", "📋 All Addresses", "🚐 Delivery Run", "🗺️ Map", "📍 Routes", "📧 Emails"
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — VOLUNTEER ROSTER
# ══════════════════════════════════════════════════════════════════════════════
with tab_roster:
    st.subheader("👥 Volunteer Roster")
    st.caption("Add all volunteers here once. Mark who is available on the Delivery Run tab.")
    roster = st.session_state.volunteer_roster
    for i, v in enumerate(roster):
        with st.container(border=True):
            col0, col1, col2, col3, col4 = st.columns([0.3, 2, 2, 3, 1])
            with col0:
                st.markdown(f'<span style="color:{HEX_COLORS[i%len(HEX_COLORS)]};font-size:22px;">&#9679;</span>', unsafe_allow_html=True)
            with col1:
                roster[i]["name"] = st.text_input("Name", value=v.get("name",""), key=f"rname_{i}", placeholder="Full name")
            with col2:
                roster[i]["email"] = st.text_input("Email", value=v.get("email",""), key=f"remail_{i}", placeholder="email@example.com")
            with col3:
                roster[i]["address"] = st.text_input("Home address", value=v.get("address",""), key=f"raddr_{i}", placeholder="123 Main St, Baltimore, MD")
            with col4:
                st.write(""); st.write("")
                if st.button("Remove", key=f"rrem_{i}"):
                    st.session_state.volunteer_roster.pop(i)
                    save_data("volunteer_roster", st.session_state.volunteer_roster)
                    st.rerun()
    c1, c2 = st.columns(2)
    with c1:
        if st.button("+ Add Volunteer", use_container_width=True):
            st.session_state.volunteer_roster.append({"name":"","email":"","address":""})
            st.rerun()
    with c2:
        if st.button("💾 Save Roster", type="primary", use_container_width=True):
            save_data("volunteer_roster", st.session_state.volunteer_roster)
            st.success("Roster saved!")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ALL ADDRESSES (master list)
# ══════════════════════════════════════════════════════════════════════════════
with tab_addresses:
    st.subheader("📋 All Addresses")
    st.caption("Master list of every address. Automatically updated when you complete stops on the Routes tab.")

    master = st.session_state.master_addresses
    delivered = [a for a in master if a.get("status") == "delivered"]
    pending   = [a for a in master if a.get("status") != "delivered"]

    # ── Add new address manually ──
    with st.expander("➕ Add new address manually", expanded=False):
        na_col1, na_col2, na_col3 = st.columns(3)
        with na_col1:
            new_addr = st.text_input("Address", key="new_addr_input", placeholder="123 Oak St, Baltimore, MD")
        with na_col2:
            new_contact = st.text_input("Contact name (optional)", key="new_contact_input")
        with na_col3:
            new_phone = st.text_input("Phone (optional)", key="new_phone_input")
        new_note = st.text_input("Note (optional)", key="new_note_input", placeholder="e.g. leave at side door")
        if st.button("Add Address", type="primary"):
            if new_addr:
                new_entry = {
                    "id": str(uuid.uuid4()),
                    "address": new_addr,
                    "contact": new_contact,
                    "phone": new_phone,
                    "note": new_note,
                    "status": "pending"
                }
                st.session_state.master_addresses.append(new_entry)
                save_data("master_addresses", st.session_state.master_addresses)
                st.success(f"Added: {new_addr}")
                st.rerun()

    st.divider()

    # ── Pending list ──
    st.markdown(f"### ⏳ Pending ({len(pending)})")
    if not pending:
        st.info("No pending addresses.")
    else:
        for a in pending:
            with st.container(border=True):
                c1, c2, c3 = st.columns([5, 2, 1])
                with c1:
                    st.markdown(f"**{a['address']}**")
                    details = []
                    if a.get("contact"): details.append(f"👤 {a['contact']}")
                    if a.get("phone"):   details.append(f"📞 {a['phone']}")
                    if a.get("note"):    details.append(f"📝 {a['note']}")
                    if details: st.caption(" · ".join(details))
                with c2:
                    if st.button("Mark Delivered", key=f"mdeliv_{a['id']}"):
                        idx = next(i for i,x in enumerate(st.session_state.master_addresses) if x["id"] == a["id"])
                        st.session_state.master_addresses[idx]["status"] = "delivered"
                        st.session_state.master_addresses[idx]["delivered_date"] = datetime.now().strftime("%b %d, %Y")
                        save_data("master_addresses", st.session_state.master_addresses)
                        st.rerun()
                with c3:
                    if st.button("✕", key=f"mdel_{a['id']}"):
                        st.session_state.master_addresses = [x for x in st.session_state.master_addresses if x["id"] != a["id"]]
                        save_data("master_addresses", st.session_state.master_addresses)
                        st.rerun()

    st.divider()

    # ── Delivered list ──
    st.markdown(f"### ✅ Signs Placed ({len(delivered)})")
    if not delivered:
        st.info("No signs placed yet.")
    else:
        for a in delivered:
            with st.container(border=True):
                c1, c2 = st.columns([7, 1])
                with c1:
                    delivered_date = a.get("delivered_date", "")
                    date_text = f" · 📅 {delivered_date}" if delivered_date else ""
                    st.markdown(f"✅ **{a['address']}**{date_text}")
                    details = []
                    if a.get("contact"): details.append(f"👤 {a['contact']}")
                    if a.get("phone"):   details.append(f"📞 {a['phone']}")
                    if a.get("note"):    details.append(f"📝 {a['note']}")
                    if details: st.caption(" · ".join(details))
                with c2:
                    if st.button("Undo", key=f"mundo_{a['id']}"):
                        idx = next(i for i,x in enumerate(st.session_state.master_addresses) if x["id"] == a["id"])
                        st.session_state.master_addresses[idx]["status"] = "pending"
                        save_data("master_addresses", st.session_state.master_addresses)
                        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — DELIVERY RUN
# ══════════════════════════════════════════════════════════════════════════════
with tab_run:
    st.subheader("🚐 Delivery Run Setup")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Who is available today?**")
        roster = st.session_state.volunteer_roster
        if not roster:
            st.info("No volunteers yet — add them in the Volunteers tab.")
        else:
            for i, v in enumerate(roster):
                if not v.get("name"): continue
                is_avail = v["name"] in st.session_state.availability
                checked = st.checkbox(
                    f"**{v['name']}** — {v.get('address','no address')}",
                    value=is_avail, key=f"avail_{i}"
                )
                if checked: st.session_state.availability.add(v["name"])
                else: st.session_state.availability.discard(v["name"])

    with col2:
        st.markdown("**Delivery Addresses for this Run**")

        # ── Search existing master addresses ──
        st.caption("Search existing addresses or add new ones.")
        search_q = st.text_input("🔍 Search master addresses", placeholder="Type street name...", key="run_search")

        if search_q:
            matches = [a for a in st.session_state.master_addresses
                       if search_q.lower() in a["address"].lower()
                       and a["id"] not in st.session_state.run_address_ids]
            if matches:
                for m in matches[:8]:
                    c1, c2 = st.columns([7, 1])
                    with c1:
                        status_icon = "✅" if m.get("status") == "delivered" else "⏳"
                        st.markdown(f"{status_icon} {m['address']}")
                    with c2:
                        if st.button("Add", key=f"srch_add_{m['id']}"):
                            st.session_state.run_address_ids.append(m["id"])
                            save_data("run_address_ids", st.session_state.run_address_ids)
                            st.rerun()
            else:
                st.caption("No matches found.")

        # ── Add brand new address ──
        with st.expander("➕ Add new address to this run", expanded=False):
            nr_col1, nr_col2 = st.columns(2)
            with nr_col1:
                nr_addr = st.text_input("Address", key="nr_addr", placeholder="123 Oak St, Baltimore, MD")
            with nr_col2:
                nr_contact = st.text_input("Contact (optional)", key="nr_contact")
            nr_phone = st.text_input("Phone (optional)", key="nr_phone")
            nr_note  = st.text_input("Note (optional)", key="nr_note", placeholder="e.g. leave at side door")
            if st.button("Add to Run + Master List", type="primary", key="nr_add"):
                if nr_addr:
                    new_entry = {
                        "id": str(uuid.uuid4()),
                        "address": nr_addr,
                        "contact": nr_contact,
                        "phone": nr_phone,
                        "note": nr_note,
                        "status": "pending"
                    }
                    st.session_state.master_addresses.append(new_entry)
                    save_data("master_addresses", st.session_state.master_addresses)
                    st.session_state.run_address_ids.append(new_entry["id"])
                    save_data("run_address_ids", st.session_state.run_address_ids)
                    st.success(f"Added: {nr_addr}")
                    st.rerun()

        # ── Current run addresses ──
        st.divider()
        run_addresses = [get_master_by_id(aid) for aid in st.session_state.run_address_ids]
        run_addresses = [a for a in run_addresses if a]
        st.markdown(f"**Current run: {len(run_addresses)} address{'es' if len(run_addresses)!=1 else ''}**")

        if not run_addresses:
            st.info("No addresses in this run yet. Search above or add a new one.")
        else:
            # Bulk import
            bulk = st.text_area("Or bulk import (one per line):", height=80, key="run_bulk",
                                placeholder="456 Elm Ave, Baltimore, MD\n789 Oak St, Baltimore, MD")
            if st.button("Import", key="run_bulk_import"):
                lines = [l.strip() for l in bulk.splitlines() if l.strip()]
                for l in lines:
                    new_entry = {"id": str(uuid.uuid4()), "address": l, "contact": "", "phone": "", "note": "", "status": "pending"}
                    st.session_state.master_addresses.append(new_entry)
                    st.session_state.run_address_ids.append(new_entry["id"])
                save_data("master_addresses", st.session_state.master_addresses)
                save_data("run_address_ids", st.session_state.run_address_ids)
                st.rerun()

            for a in run_addresses:
                c1, c2 = st.columns([8, 1])
                with c1:
                    status_icon = "✅" if a.get("status") == "delivered" else "⏳"
                    st.markdown(f"{status_icon} {a['address']}")
                with c2:
                    if st.button("✕", key=f"run_rem_{a['id']}"):
                        st.session_state.run_address_ids.remove(a["id"])
                        save_data("run_address_ids", st.session_state.run_address_ids)
                        st.rerun()

    st.divider()
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("💾 Save Run", use_container_width=True):
            save_data("run_address_ids", st.session_state.run_address_ids)
            st.success("Saved!")
    with c2:
        if st.button("🗑️ Clear Run", use_container_width=True):
            st.session_state.run_address_ids = []
            st.session_state.completed = {}
            st.session_state.routes = []
            st.session_state.availability = set()
            save_data("run_address_ids", [])
            save_data("completed", [])
            st.rerun()
    with c3:
        if st.button("🚀 Optimize Routes", type="primary", use_container_width=True):
            active_vols = [v for v in st.session_state.volunteer_roster
                           if v.get("name") and v.get("address") and v["name"] in st.session_state.availability]
            run_addrs = [get_master_by_id(aid) for aid in st.session_state.run_address_ids]
            run_addrs = [a for a in run_addrs if a and a.get("address")]

            if not active_vols:
                st.error("Please check at least one available volunteer.")
            elif not run_addrs:
                st.error("Please add at least one delivery address.")
            else:
                with st.spinner("Geocoding addresses..."):
                    vol_results = []
                    for v in active_vols:
                        lat, lng = geocode_address(v["address"])
                        if lat is None: st.error(f"Could not geocode: {v['address']}"); st.stop()
                        vol_results.append({**v, "lat": lat, "lng": lng})

                    del_results = []
                    for a in run_addrs:
                        lat, lng = geocode_address(a["address"])
                        if lat is None: st.warning(f"Skipping: {a['address']}"); continue
                        del_results.append({**a, "lat": lat, "lng": lng})

                if not del_results: st.error("No addresses could be geocoded."); st.stop()

                with st.spinner("Building driving distance matrix..."):
                    all_points = [(v["lat"],v["lng"]) for v in vol_results] + [(d["lat"],d["lng"]) for d in del_results]
                    n_vols = len(vol_results)
                    full_matrix = osrm_matrix(all_points)

                with st.spinner("Optimizing routes..."):
                    clusters = {i: [] for i in range(n_vols)}
                    for di in range(len(del_results)):
                        best_vol = min(range(n_vols), key=lambda vi: full_matrix[vi][n_vols+di])
                        clusters[best_vol].append(n_vols+di)

                    routes = []
                    for vi, vol in enumerate(vol_results):
                        if not clusters[vi]: continue
                        ordered_indices, dist_km = solve_tsp_from_home(full_matrix, vi, clusters[vi])
                        ordered_stops = [del_results[idx-n_vols] for idx in ordered_indices]
                        waypoints = [(vol["lat"],vol["lng"])] + [(s["lat"],s["lng"]) for s in ordered_stops] + [(vol["lat"],vol["lng"])]
                        routes.append({
                            "volunteer": vol,
                            "stops": ordered_stops,
                            "distance_km": dist_km,
                            "distance_miles": km_to_miles(dist_km),
                            "road_geometry": osrm_route_geometry(waypoints),
                            "color": COLORS[vi%len(COLORS)],
                            "hex": HEX_COLORS[vi%len(HEX_COLORS)],
                        })

                st.session_state.routes = routes
                st.toast(f"✅ Routes ready — {len(del_results)} deliveries across {len(routes)} volunteers", icon="🗺️")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — MAP
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
            hex_c = r["hex"]; color = r["color"]

            folium.Marker(
                location=[vol["lat"], vol["lng"]],
                popup=folium.Popup(f"<b>Home: {vol['name']}</b><br>{vol['address']}", max_width=250),
                tooltip=f"Home: {vol['name']}",
                icon=folium.Icon(color=color, icon="home", prefix="fa"),
            ).add_to(m)

            if r.get("road_geometry"):
                folium.PolyLine(r["road_geometry"], color=hex_c, weight=4, opacity=0.8,
                                tooltip=f"{vol['name']}: {r['distance_miles']} mi").add_to(m)

            for i, stop in enumerate(r["stops"]):
                prev = vol["address"] if i == 0 else r["stops"][i-1]["address"]
                key = vol["name"] + "_" + str(i)
                is_done = key in completed
                note_html = f"<br><i>Note: {stop['note']}</i>" if stop.get("note") else ""
                contact_html = f"<br>👤 {stop['contact']}" if stop.get("contact") else ""

                if is_done:
                    folium.Marker(
                        location=[stop["lat"], stop["lng"]],
                        popup=folium.Popup(f"<b>Sign Placed</b><br>{stop['address']}{contact_html}{note_html}", max_width=250),
                        tooltip=f"Sign placed — {stop['address']}",
                        icon=folium.Icon(color="green", icon="check", prefix="fa"),
                    ).add_to(m)
                else:
                    folium.Marker(
                        location=[stop["lat"], stop["lng"]],
                        popup=folium.Popup(
                            f"<b>Stop {i+1} — {vol['name']}</b><br>{stop['address']}{contact_html}{note_html}<br>"
                            f"<a href='{gmaps_dir(prev, stop['address'])}' target='_blank'>Get Directions</a>",
                            max_width=250),
                        tooltip=f"Stop {i+1} — {vol['name']}",
                        icon=folium.DivIcon(
                            html=f'<div style="background:white;color:{hex_c};border:2px solid {hex_c};border-radius:50%;width:26px;height:26px;display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:12px;box-shadow:0 2px 4px rgba(0,0,0,0.25)">{i+1}</div>',
                            icon_size=(26,26), icon_anchor=(13,13)),
                    ).add_to(m)

        # Legend
        c_a, c_b, c_rest = st.columns([1,1,6])
        c_a.markdown('<span style="color:#27ae60;font-size:20px;">&#9679;</span> Sign placed', unsafe_allow_html=True)
        c_b.markdown('<span style="color:#aaa;font-size:20px;">&#9679;</span> Pending', unsafe_allow_html=True)
        st_folium(m, use_container_width=True, height=580)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — ROUTES
# ══════════════════════════════════════════════════════════════════════════════
with tab_routes:
    if "routes" not in st.session_state or not st.session_state.routes:
        st.info("Run the optimizer first on the Delivery Run tab.")
    else:
        routes = st.session_state.routes
        completed = st.session_state.completed

        for r in routes:
            vol = r["volunteer"]; vol_name = vol["name"]
            done_count = sum(1 for i in range(len(r["stops"])) if vol_name+"_"+str(i) in completed)
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
                            # Mark completed in routes tracker
                            st.session_state.completed[key] = {"key": key, "address": s["address"],
                                "lat": s["lat"], "lng": s["lng"], "volunteer": vol_name, "stop_num": i+1,
                                "delivered_date": datetime.now().strftime("%b %d, %Y")}
                            save_data("completed", list(st.session_state.completed.values()))
                            # Also update master address status to delivered
                            if s.get("id"):
                                idx = next((j for j,a in enumerate(st.session_state.master_addresses) if a["id"]==s["id"]), None)
                                if idx is not None:
                                    st.session_state.master_addresses[idx]["status"] = "delivered"
                                    save_data("master_addresses", st.session_state.master_addresses)
                            st.rerun()
                        elif not checked and is_done:
                            del st.session_state.completed[key]
                            save_data("completed", list(st.session_state.completed.values()))
                            if s.get("id"):
                                idx = next((j for j,a in enumerate(st.session_state.master_addresses) if a["id"]==s["id"]), None)
                                if idx is not None:
                                    st.session_state.master_addresses[idx]["status"] = "pending"
                                    save_data("master_addresses", st.session_state.master_addresses)
                            st.rerun()
                    with col_info:
                        note_text = f" — *{s['note']}*" if s.get("note") else ""
                        contact_text = f" · 👤 {s['contact']}" if s.get("contact") else ""
                        if is_done:
                            delivered_date = completed[key].get("delivered_date", "")
                            date_text = f" · 📅 {delivered_date}" if delivered_date else ""
                            st.markdown(f"~~**Stop {i+1}:** {s['address']}~~ ✅{contact_text}{note_text}{date_text}")
                        else:
                            st.markdown(f"**Stop {i+1}:** {s['address']}{contact_text}{note_text}")
                        st.markdown(f"[Get Directions]({gmaps_dir(prev, s['address'])})")
                st.divider()
                st.markdown(f"**Return home:** {vol['address']}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — EMAILS
# ══════════════════════════════════════════════════════════════════════════════
with tab_emails:
    if "routes" not in st.session_state or not st.session_state.routes:
        st.info("Run the optimizer first on the Delivery Run tab.")
    else:
        st.subheader("📧 Volunteer Route Emails")
        st.caption("Click 'Open in Mail App' to send directly, or copy the text below.")
        for r in st.session_state.routes:
            vol = r["volunteer"]; vol_email = vol.get("email","")
            email_body = generate_email_body(r)
            subject = "Conway for Congress - Your Yard Sign Delivery Route"
            with st.expander("Email for " + vol["name"] + " — " + (vol_email if vol_email else "no email on file"), expanded=True):
                if vol_email:
                    st.markdown(
                        f'<a href="{mailto_link(vol_email, subject, email_body)}" style="display:inline-block;padding:10px 20px;background:#2563eb;color:white;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;">Open in Mail App</a>',
                        unsafe_allow_html=True)
                    st.caption(f"Sends to: {vol_email}")
                else:
                    st.warning("No email on file — add it in the Volunteers tab.")
                st.text_area("Or copy manually:", value=email_body, height=300, key=f"email_{vol['name']}")
