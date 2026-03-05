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
import io
from datetime import datetime
from supabase import create_client

st.set_page_config(page_title="C4C Route Optimizer", page_icon="🗺️", layout="wide")

COLORS     = ["red","blue","green","orange","purple","darkred","cadetblue","darkgreen"]
HEX_COLORS = ["#e74c3c","#3498db","#2ecc71","#f39c12","#9b59b6","#c0392b","#5f9ea0","#27ae60"]
OSRM_BASE  = "https://router.project-osrm.org"
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
def gmaps_dir(o, d): return f"https://www.google.com/maps/dir/{o.replace(' ','+')}/{d.replace(' ','+')}"
def mailto_link(to, subject, body):
    return f"mailto:{to}?" + urllib.parse.urlencode({"subject": subject, "body": body})

@st.cache_data(show_spinner=False)
def geocode_address(address: str):
    geolocator = Nominatim(user_agent="c4c_route_optimizer")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
    loc = geocode(address)
    return (loc.latitude, loc.longitude) if loc else (None, None)

# ── TSP ────────────────────────────────────────────────────────────────────────
def nearest_neighbor(dm, start=0):
    n = len(dm)
    if n <= 1: return list(range(n))
    visited = [False]*n; route = [start]; visited[start] = True
    for _ in range(n-1):
        last = route[-1]; best, bd = -1, float("inf")
        for j in range(n):
            if not visited[j] and dm[last][j] < bd: bd = dm[last][j]; best = j
        route.append(best); visited[best] = True
    return route

def two_opt(dm, route):
    improved = True
    while improved:
        improved = False; n = len(route)
        for i in range(1, n-1):
            for j in range(i+1, n):
                a,b = route[i-1],route[i]; c,d = route[j],route[(j+1)%n]
                if dm[a][b]+dm[c][d] > dm[a][c]+dm[b][d]+1e-10:
                    route[i:j+1] = route[i:j+1][::-1]; improved = True
    return route

def route_cost(fm, route, hi):
    cost = fm[hi][route[0]]
    for i in range(len(route)-1): cost += fm[route[i]][route[i+1]]
    return cost + fm[route[-1]][hi]

def solve_tsp(fm, hi, stops):
    if not stops: return [], 0.0
    n = len(stops)
    sub = [[fm[stops[i]][stops[j]] for j in range(n)] for i in range(n)]
    best_r, best_c = None, float("inf")
    for s in range(n):
        r = two_opt(sub, nearest_neighbor(sub, s))
        fr = [stops[x] for x in r]
        c = route_cost(fm, fr, hi)
        if c < best_c: best_c = c; best_r = fr
    return best_r, round(best_c, 2)

# ── Email ──────────────────────────────────────────────────────────────────────
def generate_email(route):
    vol = route["volunteer"]; stops = route["stops"]; miles = km_to_miles(route["distance_km"])
    lines = [f"Hi {vol['name']},",
             f"\nThank you for volunteering to deliver yard signs for Conway for Congress!",
             f"\nYou have {len(stops)} stop{'s' if len(stops)>1 else ''}, covering ~{miles} miles:\n"]
    for i, s in enumerate(stops):
        prev = vol["address"] if i==0 else stops[i-1]["address"]
        note = f" -- Note: {s['note']}" if s.get("note") else ""
        lines += [f"  Stop {i+1}: {s['address']}{note}", f"  Directions: {gmaps_dir(prev, s['address'])}\n"]
    lines += [f"Return home: {vol['address']}", f"\nTotal: ~{miles} miles",
              "\nThank you!\nConway for Congress Team"]
    return "\n".join(lines)

# ── CSV import helper ──────────────────────────────────────────────────────────
FIELD_CANDIDATES = {
    "address": ["address","street_address","mailing_address","primary_address",
                "addr","street","address1","full_address","residential_address"],
    "first_name": ["first_name","firstname","first","fname","given_name"],
    "last_name":  ["last_name","lastname","last","lname","surname","family_name"],
    "email":      ["email","email_address","e_mail","emailaddress"],
    "phone":      ["phone","phone_number","mobile","cell","telephone","mobile_number","phone1"],
    "city":       ["city","town","municipality"],
    "state":      ["state","state_code","province"],
    "zip":        ["zip","zipcode","zip_code","postal_code","postcode"],
}

def detect_column(df_cols, candidates):
    cols_lower = {c.lower().strip().replace(" ","_"): c for c in df_cols}
    for cand in candidates:
        if cand in cols_lower: return cols_lower[cand]
    return None

def parse_csv(uploaded_file):
    """Parse a CSV from NationBuilder, NGP VAN, or Action Network and return list of address dicts."""
    df = pd.read_csv(uploaded_file, dtype=str).fillna("")
    results = []
    addr_col  = detect_column(df.columns, FIELD_CANDIDATES["address"])
    fname_col = detect_column(df.columns, FIELD_CANDIDATES["first_name"])
    lname_col = detect_column(df.columns, FIELD_CANDIDATES["last_name"])
    email_col = detect_column(df.columns, FIELD_CANDIDATES["email"])
    phone_col = detect_column(df.columns, FIELD_CANDIDATES["phone"])
    city_col  = detect_column(df.columns, FIELD_CANDIDATES["city"])
    state_col = detect_column(df.columns, FIELD_CANDIDATES["state"])
    zip_col   = detect_column(df.columns, FIELD_CANDIDATES["zip"])

    for _, row in df.iterrows():
        addr = row[addr_col].strip() if addr_col else ""
        if not addr: continue
        # Build full address if city/state/zip are separate columns
        if city_col or state_col or zip_col:
            city  = row[city_col].strip()  if city_col  else ""
            state = row[state_col].strip() if state_col else ""
            zp    = row[zip_col].strip()   if zip_col   else ""
            parts = [p for p in [city, state, zp] if p]
            if parts: addr = addr + ", " + ", ".join(parts)
        fname = row[fname_col].strip() if fname_col else ""
        lname = row[lname_col].strip() if lname_col else ""
        contact = (fname + " " + lname).strip()
        results.append({
            "id":      str(uuid.uuid4()),
            "address": addr,
            "contact": contact,
            "phone":   row[phone_col].strip() if phone_col else "",
            "note":    "",
            "status":  "pending",
        })
    return results

# ── Load data ──────────────────────────────────────────────────────────────────
if "loaded" not in st.session_state:
    st.session_state.volunteer_roster = load_data("volunteer_roster") or []
    st.session_state.master_addresses = load_data("master_addresses") or []
    st.session_state.run_address_ids  = load_data("run_address_ids")  or []
    st.session_state.completed        = {c["key"]: c for c in (load_data("completed") or [])}
    st.session_state.route_history    = load_data("route_history")    or []
    st.session_state.routes           = st.session_state.route_history[0]["routes"] if st.session_state.route_history else []
    st.session_state.availability     = set()
    st.session_state.new_vol          = {"name":"","email":"","address":""}
    st.session_state.loaded = True

def get_master_by_id(aid):
    return next((a for a in st.session_state.master_addresses if a["id"] == aid), None)

# ── UI ─────────────────────────────────────────────────────────────────────────
st.title("🗺️ Conway for Congress — Yard Sign Route Optimizer")

tab_roster, tab_addresses, tab_run, tab_map, tab_routes, tab_emails = st.tabs([
    "👥 Volunteers", "📋 All Addresses", "🚐 Delivery Run", "🗺️ Map", "📍 Routes", "📧 Emails"
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — VOLUNTEERS
# ══════════════════════════════════════════════════════════════════════════════
with tab_roster:
    st.subheader("👥 Volunteer Roster")

    # ── Add new volunteer manually ──
    with st.container(border=True):
        st.markdown("**Add New Volunteer**")
        c1, c2 = st.columns(2)
        with c1:
            new_name  = st.text_input("Full Name*", key="new_vol_name", placeholder="Jane Smith")
            new_email = st.text_input("Email", key="new_vol_email", placeholder="jane@email.com")
            new_phone = st.text_input("Phone", key="new_vol_phone", placeholder="410-555-0100")
        with c2:
            new_street = st.text_input("Street Address*", key="new_vol_street", placeholder="123 Main St")
            nc1, nc2, nc3 = st.columns(3)
            with nc1: new_city  = st.text_input("City*",  key="new_vol_city",  placeholder="Baltimore")
            with nc2: new_state = st.text_input("State*", key="new_vol_state", placeholder="MD")
            with nc3: new_zip   = st.text_input("ZIP*",   key="new_vol_zip",   placeholder="21201")
        if st.button("➕ Add to Roster", type="primary"):
            if new_name and new_street and new_city and new_state and new_zip:
                full_addr = f"{new_street}, {new_city}, {new_state} {new_zip}"
                st.session_state.volunteer_roster.append({
                    "name": new_name, "email": new_email,
                    "phone": new_phone, "address": full_addr
                })
                save_data("volunteer_roster", st.session_state.volunteer_roster)
                st.toast(f"✅ {new_name} added to roster!", icon="👤")
                st.rerun()
            else:
                st.warning("Name, street, city, state, and ZIP are required.")

    st.divider()

    # ── CSV import ──
    with st.expander("📂 Import Volunteers from CSV (NationBuilder, NGP VAN, Action Network)", expanded=False):
        st.caption("Upload a CSV export from your campaign database. The system will auto-detect name, email, phone, and address columns.")
        vol_csv = st.file_uploader("Upload CSV", type=["csv"], key="vol_csv_upload")
        if vol_csv:
            try:
                df_v = pd.read_csv(vol_csv, dtype=str).fillna("")
                fname_col = detect_column(df_v.columns, FIELD_CANDIDATES["first_name"])
                lname_col = detect_column(df_v.columns, FIELD_CANDIDATES["last_name"])
                email_col = detect_column(df_v.columns, FIELD_CANDIDATES["email"])
                phone_col = detect_column(df_v.columns, FIELD_CANDIDATES["phone"])
                addr_col  = detect_column(df_v.columns, FIELD_CANDIDATES["address"])
                city_col  = detect_column(df_v.columns, FIELD_CANDIDATES["city"])
                state_col = detect_column(df_v.columns, FIELD_CANDIDATES["state"])
                zip_col   = detect_column(df_v.columns, FIELD_CANDIDATES["zip"])

                parsed_vols = []
                for _, row in df_v.iterrows():
                    fname = row[fname_col].strip() if fname_col else ""
                    lname = row[lname_col].strip() if lname_col else ""
                    name  = (fname + " " + lname).strip()
                    if not name: continue
                    addr = row[addr_col].strip() if addr_col else ""
                    city  = row[city_col].strip()  if city_col  else ""
                    state = row[state_col].strip() if state_col else ""
                    zp    = row[zip_col].strip()   if zip_col   else ""
                    if city or state or zp:
                        addr = addr + ", " + ", ".join(p for p in [city, state, zp] if p)
                    parsed_vols.append({
                        "name":    name,
                        "email":   row[email_col].strip() if email_col else "",
                        "phone":   row[phone_col].strip() if phone_col else "",
                        "address": addr,
                    })

                st.success(f"Found {len(parsed_vols)} volunteers in CSV.")
                st.dataframe(pd.DataFrame(parsed_vols), use_container_width=True, hide_index=True)
                if st.button("✅ Import All Volunteers", type="primary", key="import_vols"):
                    existing = {v["name"].lower() for v in st.session_state.volunteer_roster}
                    added = 0
                    for pv in parsed_vols:
                        if pv["name"].lower() not in existing:
                            st.session_state.volunteer_roster.append(pv)
                            added += 1
                    save_data("volunteer_roster", st.session_state.volunteer_roster)
                    st.toast(f"✅ Imported {added} volunteers!", icon="👥")
                    st.rerun()
            except Exception as e:
                st.error(f"Could not parse CSV: {e}")

    st.divider()

    # ── Roster table ──
    roster = st.session_state.volunteer_roster
    if not roster:
        st.info("No volunteers yet. Add one above.")
    else:
        with st.expander(f"📋 View All Volunteers ({len(roster)})", expanded=True):
            df = pd.DataFrame([{
                "Name":    v.get("name",""),
                "Email":   v.get("email",""),
                "Phone":   v.get("phone",""),
                "Address": v.get("address",""),
            } for v in roster])
            edited = st.data_editor(df, use_container_width=True, hide_index=False,
                                    num_rows="fixed", key="roster_editor")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("💾 Save Changes", use_container_width=True):
                    updated = []
                    for i, row in edited.iterrows():
                        updated.append({"name": row["Name"], "email": row["Email"],
                                        "phone": row.get("Phone",""), "address": row["Address"]})
                    st.session_state.volunteer_roster = updated
                    save_data("volunteer_roster", updated)
                    st.toast("Roster saved!", icon="💾")
            with c2:
                del_idx = st.selectbox("Remove volunteer", options=["—"] + [v["name"] for v in roster], key="del_vol")
                if st.button("🗑️ Remove", use_container_width=True):
                    if del_idx != "—":
                        st.session_state.volunteer_roster = [v for v in roster if v["name"] != del_idx]
                        save_data("volunteer_roster", st.session_state.volunteer_roster)
                        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ALL ADDRESSES
# ══════════════════════════════════════════════════════════════════════════════
with tab_addresses:
    st.subheader("📋 All Addresses")
    st.caption("Master list of every address. Updated automatically when stops are completed.")

    master = st.session_state.master_addresses
    delivered = [a for a in master if a.get("status") == "delivered"]
    pending   = [a for a in master if a.get("status") != "delivered"]

    # ── CSV Import ──
    with st.expander("📂 Import from Campaign Database (NationBuilder, NGP VAN, Action Network)", expanded=False):
        st.caption("Upload a CSV export from your campaign database. The system will auto-detect address, name, phone, and email columns.")
        uploaded = st.file_uploader("Upload CSV", type=["csv"], key="csv_upload")
        if uploaded:
            try:
                parsed = parse_csv(uploaded)
                st.success(f"Found {len(parsed)} addresses in CSV.")
                preview_df = pd.DataFrame([{"Address": p["address"], "Contact": p["contact"], "Phone": p["phone"]} for p in parsed])
                st.dataframe(preview_df, use_container_width=True, hide_index=True)
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("✅ Import All to Master List", type="primary"):
                        existing_addrs = {a["address"].lower() for a in st.session_state.master_addresses}
                        added = 0
                        for p in parsed:
                            if p["address"].lower() not in existing_addrs:
                                st.session_state.master_addresses.append(p)
                                added += 1
                        save_data("master_addresses", st.session_state.master_addresses)
                        st.toast(f"✅ Imported {added} new addresses!", icon="📂")
                        st.rerun()
                with c2:
                    if st.button("➕ Also Add to Current Delivery Run"):
                        existing_addrs = {a["address"].lower() for a in st.session_state.master_addresses}
                        for p in parsed:
                            if p["address"].lower() not in existing_addrs:
                                st.session_state.master_addresses.append(p)
                            entry = next((a for a in st.session_state.master_addresses if a["address"].lower() == p["address"].lower()), None)
                            if entry and entry["id"] not in st.session_state.run_address_ids:
                                st.session_state.run_address_ids.append(entry["id"])
                        save_data("master_addresses", st.session_state.master_addresses)
                        save_data("run_address_ids", st.session_state.run_address_ids)
                        st.toast("Added to master list and current run!", icon="🚐")
                        st.rerun()
            except Exception as e:
                st.error(f"Could not parse CSV: {e}")

    # ── Add manually ──
    with st.expander("➕ Add address manually", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            na_street  = st.text_input("Street Address*", key="na_street", placeholder="123 Oak St")
            na_contact = st.text_input("Contact Name",    key="na_contact", placeholder="John Smith")
            na_phone   = st.text_input("Phone",           key="na_phone",   placeholder="410-555-0100")
        with c2:
            nc1, nc2, nc3 = st.columns(3)
            with nc1: na_city  = st.text_input("City*",  key="na_city",  placeholder="Baltimore")
            with nc2: na_state = st.text_input("State*", key="na_state", placeholder="MD")
            with nc3: na_zip   = st.text_input("ZIP*",   key="na_zip",   placeholder="21201")
            na_email = st.text_input("Email", key="na_email", placeholder="john@email.com")
            na_note  = st.text_input("Note",  key="na_note",  placeholder="e.g. leave at side door")
        if st.button("Add Address", type="primary", key="na_add"):
            if na_street and na_city and na_state and na_zip:
                full_addr = f"{na_street}, {na_city}, {na_state} {na_zip}"
                st.session_state.master_addresses.append({
                    "id": str(uuid.uuid4()), "address": full_addr,
                    "contact": na_contact, "phone": na_phone,
                    "email": na_email, "note": na_note, "status": "pending"
                })
                save_data("master_addresses", st.session_state.master_addresses)
                st.toast(f"Added: {full_addr}", icon="📍")
                st.rerun()
            else:
                st.warning("Street, city, state, and ZIP are required.")

    st.divider()

    # ── All addresses spreadsheet ──
    st.markdown(f"### 📋 All Addresses — {len(master)} total")
    if not master:
        st.info("No addresses yet.")
    else:
        all_df = pd.DataFrame([{
            "Status":         "✅ Placed" if a.get("status")=="delivered" else "⏳ Pending",
            "Address":        a.get("address",""),
            "Contact":        a.get("contact",""),
            "Phone":          a.get("phone",""),
            "Email":          a.get("email",""),
            "Date Delivered": a.get("delivered_date",""),
            "Note":           a.get("note",""),
        } for a in master])
        st.dataframe(all_df, use_container_width=True, hide_index=True,
                     column_config={
                         "Status": st.column_config.TextColumn(width="small"),
                         "Date Delivered": st.column_config.TextColumn(width="medium"),
                     })
        # Download button
        csv_bytes = all_df.to_csv(index=False).encode()
        st.download_button("⬇️ Export all as CSV", data=csv_bytes,
                           file_name="c4c_all_addresses.csv", mime="text/csv")

    st.divider()

    # ── Pending spreadsheet ──
    st.markdown(f"### ⏳ Pending — {len(pending)} address{'es' if len(pending)!=1 else ''}")
    if not pending:
        st.info("No pending addresses.")
    else:
        pending_df = pd.DataFrame([{
            "Address":  a.get("address",""),
            "Contact":  a.get("contact",""),
            "Phone":    a.get("phone",""),
            "Email":    a.get("email",""),
            "Note":     a.get("note",""),
            "_id":      a.get("id",""),
        } for a in pending])
        pending_edited = st.data_editor(
            pending_df.drop(columns=["_id"]),
            use_container_width=True, hide_index=True,
            num_rows="fixed", key="pending_editor"
        )
        pc1, pc2, pc3 = st.columns(3)
        with pc1:
            if st.button("💾 Save Pending Edits", use_container_width=True):
                for i, row in pending_edited.iterrows():
                    mid = pending_df.iloc[i]["_id"]
                    idx = next((j for j,a in enumerate(st.session_state.master_addresses) if a["id"]==mid), None)
                    if idx is not None:
                        st.session_state.master_addresses[idx].update({
                            "address": row["Address"], "contact": row["Contact"],
                            "phone": row["Phone"], "email": row["Email"], "note": row["Note"]
                        })
                save_data("master_addresses", st.session_state.master_addresses)
                st.toast("Pending list saved!", icon="💾")
        with pc2:
            mark_addr = st.selectbox("Mark as delivered", options=["—"] + [a["address"] for a in pending], key="mark_del_sel")
            if st.button("✅ Mark Delivered", use_container_width=True):
                if mark_addr != "—":
                    idx = next((j for j,a in enumerate(st.session_state.master_addresses) if a["address"]==mark_addr), None)
                    if idx is not None:
                        st.session_state.master_addresses[idx]["status"] = "delivered"
                        st.session_state.master_addresses[idx]["delivered_date"] = datetime.now().strftime("%b %d, %Y")
                        save_data("master_addresses", st.session_state.master_addresses)
                        st.rerun()
        with pc3:
            del_addr = st.selectbox("Remove address", options=["—"] + [a["address"] for a in pending], key="del_pend_sel")
            if st.button("🗑️ Remove", use_container_width=True, key="del_pend_btn"):
                if del_addr != "—":
                    st.session_state.master_addresses = [a for a in st.session_state.master_addresses if a["address"]!=del_addr]
                    save_data("master_addresses", st.session_state.master_addresses)
                    st.rerun()

    st.divider()

    # ── Delivered spreadsheet ──
    st.markdown(f"### ✅ Signs Placed — {len(delivered)} address{'es' if len(delivered)!=1 else ''}")
    if not delivered:
        st.info("No signs placed yet.")
    else:
        delivered_df = pd.DataFrame([{
            "Address":        a.get("address",""),
            "Contact":        a.get("contact",""),
            "Phone":          a.get("phone",""),
            "Email":          a.get("email",""),
            "Date Delivered": a.get("delivered_date",""),
            "Note":           a.get("note",""),
            "_id":            a.get("id",""),
        } for a in delivered])
        st.dataframe(
            delivered_df.drop(columns=["_id"]),
            use_container_width=True, hide_index=True
        )
        undo_addr = st.selectbox("Undo delivery", options=["—"] + [a["address"] for a in delivered], key="undo_del_sel")
        if st.button("↩️ Undo", use_container_width=False, key="undo_del_btn"):
            if undo_addr != "—":
                idx = next((j for j,a in enumerate(st.session_state.master_addresses) if a["address"]==undo_addr), None)
                if idx is not None:
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
            # Check if we have a search address to sort by
            search_addr = st.session_state.get("run_search", "")
            sorted_roster = list(enumerate(roster))

            if search_addr and len(search_addr) > 5:
                # Geocode the search address and sort volunteers by proximity
                slat, slng = geocode_address(search_addr)
                if slat:
                    def vol_dist(iv):
                        i, v = iv
                        vlat, vlng = geocode_address(v["address"]) if v.get("address") else (None, None)
                        if vlat:
                            return haversine((slat, slng), (vlat, vlng))
                        return float("inf")
                    sorted_roster = sorted(sorted_roster, key=vol_dist)
                    st.caption("📍 Sorted by proximity to delivery address")
                else:
                    st.caption("Volunteers — check who is available")
            else:
                st.caption("Volunteers — check who is available. Type a delivery address to sort by proximity.")

            for i, v in sorted_roster:
                if not v.get("name"): continue
                checked = st.checkbox(
                    f"**{v['name']}** — {v.get('address','no address')}",
                    value=v["name"] in st.session_state.availability,
                    key=f"avail_{i}"
                )
                if checked: st.session_state.availability.add(v["name"])
                else: st.session_state.availability.discard(v["name"])

    with col2:
        st.markdown("**Delivery Addresses for this Run**")

        # Search master list
        search_q = st.text_input("🔍 Search saved addresses", placeholder="Type street name...", key="run_search")
        if search_q:
            matches = [a for a in st.session_state.master_addresses
                       if search_q.lower() in a["address"].lower()
                       and a["id"] not in st.session_state.run_address_ids]
            for m in matches[:6]:
                c1, c2 = st.columns([7,1])
                with c1:
                    icon = "✅" if m.get("status")=="delivered" else "⏳"
                    contact = f" · {m['contact']}" if m.get("contact") else ""
                    st.markdown(f"{icon} {m['address']}{contact}")
                with c2:
                    if st.button("Add", key=f"sa_{m['id']}"):
                        st.session_state.run_address_ids.append(m["id"])
                        save_data("run_address_ids", st.session_state.run_address_ids)
                        st.rerun()
            if not matches:
                st.caption("No matches found in saved addresses.")

        # Add new address
        with st.expander("➕ Add new address", expanded=False):
            c1, c2 = st.columns(2)
            with c1: nr_a = st.text_input("Address*", key="nr_addr", placeholder="123 Oak St, Baltimore, MD")
            with c2: nr_c = st.text_input("Contact", key="nr_contact")
            nr_p = st.text_input("Phone", key="nr_phone")
            nr_n = st.text_input("Note", key="nr_note", placeholder="e.g. leave at side door")
            if st.button("Add to Run + Master List", type="primary", key="nr_add"):
                if nr_a:
                    e = {"id":str(uuid.uuid4()),"address":nr_a,"contact":nr_c,"phone":nr_p,"note":nr_n,"status":"pending"}
                    st.session_state.master_addresses.append(e)
                    save_data("master_addresses", st.session_state.master_addresses)
                    st.session_state.run_address_ids.append(e["id"])
                    save_data("run_address_ids", st.session_state.run_address_ids)
                    st.toast(f"Added: {nr_a}", icon="📍")
                    st.rerun()

        # Bulk import
        with st.expander("📋 Bulk import addresses", expanded=False):
            bulk = st.text_area("One address per line:", height=80, key="run_bulk",
                                placeholder="456 Elm Ave, Baltimore, MD\n789 Oak St, Baltimore, MD")
            if st.button("Import", key="run_bulk_btn"):
                lines = [l.strip() for l in bulk.splitlines() if l.strip()]
                for l in lines:
                    e = {"id":str(uuid.uuid4()),"address":l,"contact":"","phone":"","note":"","status":"pending"}
                    st.session_state.master_addresses.append(e)
                    st.session_state.run_address_ids.append(e["id"])
                save_data("master_addresses", st.session_state.master_addresses)
                save_data("run_address_ids", st.session_state.run_address_ids)
                st.toast(f"Imported {len(lines)} addresses", icon="📋")
                st.rerun()

        # Current run list
        st.divider()
        run_addrs = [get_master_by_id(aid) for aid in st.session_state.run_address_ids]
        run_addrs = [a for a in run_addrs if a]
        st.markdown(f"**Current run: {len(run_addrs)} stop{'s' if len(run_addrs)!=1 else ''}**")
        for a in run_addrs:
            c1, c2 = st.columns([8,1])
            with c1:
                icon = "✅" if a.get("status")=="delivered" else "⏳"
                st.markdown(f"{icon} {a['address']}")
            with c2:
                if st.button("✕", key=f"rr_{a['id']}"):
                    st.session_state.run_address_ids.remove(a["id"])
                    save_data("run_address_ids", st.session_state.run_address_ids)
                    st.rerun()

    st.divider()
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("💾 Save Run", use_container_width=True):
            save_data("run_address_ids", st.session_state.run_address_ids)
            st.toast("Run saved!", icon="💾")
    with c2:
        if st.button("🗑️ Clear Run", use_container_width=True):
            st.session_state.run_address_ids = []
            st.session_state.availability = set()
            save_data("run_address_ids", [])
            st.rerun()
    with c3:
        if st.button("🚀 Optimize Routes", type="primary", use_container_width=True):
            active_vols = [v for v in st.session_state.volunteer_roster
                           if v.get("name") and v.get("address") and v["name"] in st.session_state.availability]
            run_addrs = [get_master_by_id(aid) for aid in st.session_state.run_address_ids]
            run_addrs = [a for a in run_addrs if a and a.get("address")]

            if not active_vols: st.error("Check at least one available volunteer."); st.stop()
            if not run_addrs:   st.error("Add at least one delivery address."); st.stop()

            with st.spinner("Geocoding..."):
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

            with st.spinner("Building distance matrix..."):
                all_pts = [(v["lat"],v["lng"]) for v in vol_results] + [(d["lat"],d["lng"]) for d in del_results]
                n_vols = len(vol_results)
                fm = osrm_matrix(all_pts)

            with st.spinner("Optimizing routes..."):
                clusters = {i: [] for i in range(n_vols)}
                for di in range(len(del_results)):
                    bv = min(range(n_vols), key=lambda vi: fm[vi][n_vols+di])
                    clusters[bv].append(n_vols+di)
                routes = []
                for vi, vol in enumerate(vol_results):
                    if not clusters[vi]: continue
                    oi, dk = solve_tsp(fm, vi, clusters[vi])
                    os_ = [del_results[idx-n_vols] for idx in oi]
                    wps = [(vol["lat"],vol["lng"])] + [(s["lat"],s["lng"]) for s in os_] + [(vol["lat"],vol["lng"])]
                    routes.append({
                        "volunteer": vol, "stops": os_,
                        "distance_km": dk, "distance_miles": km_to_miles(dk),
                        "road_geometry": osrm_route_geometry(wps),
                        "color": COLORS[vi%len(COLORS)], "hex": HEX_COLORS[vi%len(HEX_COLORS)],
                    })

            st.session_state.routes = routes
            run_record = {"timestamp": datetime.now().strftime("%b %d, %Y at %I:%M %p"), "routes": routes}
            st.session_state.route_history = [run_record] + (st.session_state.route_history or [])
            save_data("route_history", st.session_state.route_history)
            st.toast(f"Routes ready — {len(del_results)} stops across {len(routes)} volunteers", icon="🗺️")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — MAP
# ══════════════════════════════════════════════════════════════════════════════
with tab_map:
    master    = st.session_state.master_addresses
    completed = st.session_state.completed
    # routes only exist in session state for the person who just optimized
    active_routes = st.session_state.get("routes", [])

    center = [39.2904, -76.6122]
    m = folium.Map(location=center, zoom_start=11, tiles="CartoDB positron")

    if active_routes:
        # ── ACTIVE DELIVERY RUN VIEW ──
        # Only shown to the user who just ran the optimizer (session state only)
        # Show route lines + numbered stops for this run only
        # No master address pins — just this run
        all_lats, all_lngs = [], []
        for r in active_routes:
            vol   = r["volunteer"]
            hex_c = r["hex"]
            color = r["color"]
            all_lats.append(vol["lat"]); all_lngs.append(vol["lng"])

            folium.Marker(
                location=[vol["lat"], vol["lng"]],
                popup=folium.Popup(f"<b>Home: {vol['name']}</b><br>{vol['address']}", max_width=220),
                tooltip=f"Home: {vol['name']}",
                icon=folium.Icon(color=color, icon="home", prefix="fa"),
            ).add_to(m)

            if r.get("road_geometry"):
                folium.PolyLine(r["road_geometry"], color=hex_c, weight=4, opacity=0.8,
                                tooltip=f"{vol['name']}: {r['distance_miles']} mi").add_to(m)

            for i, stop in enumerate(r["stops"]):
                prev   = vol["address"] if i == 0 else r["stops"][i-1]["address"]
                key    = vol["name"] + "_" + str(i)
                is_done = key in completed
                note_html    = f"<br><i>📝 {stop['note']}</i>" if stop.get("note") else ""
                contact_html = f"<br>👤 {stop['contact']}" if stop.get("contact") else ""
                all_lats.append(stop["lat"]); all_lngs.append(stop["lng"])

                if is_done:
                    folium.Marker(
                        location=[stop["lat"], stop["lng"]],
                        popup=folium.Popup(f"<b>✅ Sign Placed</b><br>{stop['address']}{contact_html}{note_html}", max_width=230),
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
                            html=f'<div style="background:white;color:{hex_c};border:2px solid {hex_c};border-radius:50%;width:26px;height:26px;display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:12px;box-shadow:0 2px 4px rgba(0,0,0,.25)">{i+1}</div>',
                            icon_size=(26,26), icon_anchor=(13,13)),
                    ).add_to(m)

        if all_lats:
            m.location = [sum(all_lats)/len(all_lats), sum(all_lngs)/len(all_lngs)]
            m.zoom_start = 12

        ca, cb, cc, _ = st.columns([1.2, 1, 1.5, 4])
        ca.markdown('<span style="color:#27ae60;font-size:20px;">&#9679;</span> Sign placed', unsafe_allow_html=True)
        cb.markdown('<span style="color:#aaa;font-size:20px;">&#9679;</span> Pending stop', unsafe_allow_html=True)
        cc.markdown('<span style="color:#555;font-size:13px;">📍 Your active delivery run</span>', unsafe_allow_html=True)
        if st.button("🗺️ Switch to master map view"):
            st.session_state.routes = []
            st.rerun()

    else:
        # ── DEFAULT MASTER MAP VIEW ──
        # Geocode any addresses missing lat/lng and save them
        needs_save = False
        for i, a in enumerate(master):
            if not a.get("lat") or not a.get("lng"):
                lat, lng = geocode_address(a["address"])
                if lat:
                    st.session_state.master_addresses[i]["lat"] = lat
                    st.session_state.master_addresses[i]["lng"] = lng
                    needs_save = True
        if needs_save:
            save_data("master_addresses", st.session_state.master_addresses)

        geocoded = [a for a in st.session_state.master_addresses if a.get("lat") and a.get("lng")]
        delivered_pins = [a for a in geocoded if a.get("status") == "delivered"]
        pending_pins   = [a for a in geocoded if a.get("status") != "delivered"]

        if geocoded:
            lats = [a["lat"] for a in geocoded]; lngs = [a["lng"] for a in geocoded]
            m.location = [sum(lats)/len(lats), sum(lngs)/len(lngs)]
            m.zoom_start = 12

        for a in delivered_pins:
            date_html    = f"<br>📅 {a['delivered_date']}" if a.get("delivered_date") else ""
            contact_html = f"<br>👤 {a['contact']}" if a.get("contact") else ""
            note_html    = f"<br>📝 {a['note']}" if a.get("note") else ""
            folium.Marker(
                location=[a["lat"], a["lng"]],
                popup=folium.Popup(f"<b>✅ Sign Placed</b><br>{a['address']}{contact_html}{note_html}{date_html}", max_width=230),
                tooltip=f"Sign placed — {a['address']}",
                icon=folium.Icon(color="green", icon="check", prefix="fa"),
            ).add_to(m)

        for a in pending_pins:
            contact_html = f"<br>👤 {a['contact']}" if a.get("contact") else ""
            note_html    = f"<br>📝 {a['note']}" if a.get("note") else ""
            folium.CircleMarker(
                location=[a["lat"], a["lng"]],
                radius=8, color="#888", fill=True, fill_color="#bbb", fill_opacity=0.9,
                popup=folium.Popup(f"<b>⏳ Pending</b><br>{a['address']}{contact_html}{note_html}", max_width=230),
                tooltip=f"Pending — {a['address']}",
            ).add_to(m)

        ca, cb, _ = st.columns([1, 1, 6])
        ca.markdown('<span style="color:#27ae60;font-size:20px;">&#9679;</span> Sign placed', unsafe_allow_html=True)
        cb.markdown('<span style="color:#888;font-size:20px;">&#9679;</span> Pending', unsafe_allow_html=True)

        if not master:
            st.info("No addresses yet. Add some in the All Addresses or Delivery Run tab.")

    st_folium(m, use_container_width=True, height=580)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — ROUTES (full history, delete button per run)
# ══════════════════════════════════════════════════════════════════════════════
with tab_routes:
    if not st.session_state.route_history:
        st.info("No routes yet. Run the optimizer on the Delivery Run tab.")
    else:
        completed = st.session_state.completed
        for run_idx, run_record in enumerate(st.session_state.route_history):
            timestamp = run_record.get("timestamp","Unknown date")
            routes = run_record["routes"]

            # Header row with delete button
            hc1, hc2 = st.columns([7, 2])
            with hc1:
                label = "🟢 Most recent run" if run_idx == 0 else f"Run #{len(st.session_state.route_history)-run_idx}"
                st.markdown(f"### 📅 {timestamp}")
                st.caption(label)
            with hc2:
                st.write("")
                confirm_key = f"confirm_del_{run_idx}"
                if st.session_state.get(confirm_key):
                    cc1, cc2 = st.columns(2)
                    with cc1:
                        if st.button("✅ Yes, delete", key=f"yes_del_{run_idx}", type="primary"):
                            st.session_state.route_history.pop(run_idx)
                            save_data("route_history", st.session_state.route_history)
                            st.session_state.routes = st.session_state.route_history[0]["routes"] if st.session_state.route_history else []
                            st.session_state[confirm_key] = False
                            st.rerun()
                    with cc2:
                        if st.button("Cancel", key=f"cancel_del_{run_idx}"):
                            st.session_state[confirm_key] = False
                            st.rerun()
                else:
                    if st.button("🗑️ Delete Run", key=f"del_run_{run_idx}", use_container_width=True):
                        st.session_state[confirm_key] = True
                        st.rerun()

            for r in routes:
                vol = r["volunteer"]; vol_name = vol["name"]
                with st.expander(vol_name + " — " + str(len(r["stops"])) + " stops", expanded=(run_idx==0)):
                    st.markdown(f"**Start:** {vol['address']}")
                    st.divider()
                    for i, s in enumerate(r["stops"]):
                        prev = vol["address"] if i==0 else r["stops"][i-1]["address"]
                        key = vol_name+"_"+str(i)
                        is_done = key in completed
                        cc, ci = st.columns([1,9])
                        with cc:
                            checked = st.checkbox("", value=is_done, key=f"chk_{run_idx}_{key}")
                            if checked and not is_done:
                                st.session_state.completed[key] = {
                                    "key": key, "address": s["address"],
                                    "lat": s.get("lat"), "lng": s.get("lng"),
                                    "volunteer": vol_name, "stop_num": i+1,
                                    "delivered_date": datetime.now().strftime("%b %d, %Y")
                                }
                                save_data("completed", list(st.session_state.completed.values()))
                                mid = s.get("id")
                                if mid:
                                    idx = next((j for j,a in enumerate(st.session_state.master_addresses) if a["id"]==mid), None)
                                    if idx is not None:
                                        st.session_state.master_addresses[idx]["status"] = "delivered"
                                        st.session_state.master_addresses[idx]["delivered_date"] = datetime.now().strftime("%b %d, %Y")
                                        save_data("master_addresses", st.session_state.master_addresses)
                                st.rerun()
                            elif not checked and is_done:
                                del st.session_state.completed[key]
                                save_data("completed", list(st.session_state.completed.values()))
                                mid = s.get("id")
                                if mid:
                                    idx = next((j for j,a in enumerate(st.session_state.master_addresses) if a["id"]==mid), None)
                                    if idx is not None:
                                        st.session_state.master_addresses[idx]["status"] = "pending"
                                        save_data("master_addresses", st.session_state.master_addresses)
                                st.rerun()
                        with ci:
                            note_t = f" — *{s['note']}*" if s.get("note") else ""
                            cont_t = f" · 👤 {s['contact']}" if s.get("contact") else ""
                            if is_done:
                                dd = completed[key].get("delivered_date","")
                                dt = f" · 📅 {dd}" if dd else ""
                                st.markdown(f"~~**Stop {i+1}:** {s['address']}~~ ✅{cont_t}{note_t}{dt}")
                            else:
                                st.markdown(f"**Stop {i+1}:** {s['address']}{cont_t}{note_t}")
                            st.markdown(f"[Get Directions]({gmaps_dir(prev, s['address'])})")
                    st.divider()
                    st.markdown(f"**Return home:** {vol['address']}")
            st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — EMAILS
# ══════════════════════════════════════════════════════════════════════════════
with tab_emails:
    if not st.session_state.get("routes"):
        st.info("Run the optimizer first on the Delivery Run tab.")
    else:
        st.subheader("📧 Volunteer Route Emails")
        for r in st.session_state.routes:
            vol = r["volunteer"]; ve = vol.get("email","")
            body = generate_email(r)
            subj = "Conway for Congress - Your Yard Sign Delivery Route"
            with st.expander("Email for " + vol["name"] + " — " + (ve if ve else "no email on file"), expanded=True):
                if ve:
                    st.markdown(
                        f'<a href="{mailto_link(ve, subj, body)}" style="display:inline-block;padding:10px 20px;'
                        f'background:#2563eb;color:white;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;">'
                        f'Open in Mail App</a>', unsafe_allow_html=True)
                    st.caption(f"Sends to: {ve}")
                else:
                    st.warning("No email on file — add it in the Volunteers tab.")
                st.text_area("Or copy manually:", value=body, height=300, key=f"email_{vol['name']}")
