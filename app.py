import streamlit as st
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
import pandas as pd
import math
import requests
import urllib.parse
import uuid
import io
import hashlib
from datetime import datetime
from supabase import create_client

st.set_page_config(page_title="Campaign Route Optimizer", page_icon="🗺️", layout="wide")

COLORS     = ["red","blue","green","orange","purple","darkred","cadetblue","darkgreen"]
HEX_COLORS = ["#e74c3c","#3498db","#2ecc71","#f39c12","#9b59b6","#c0392b","#5f9ea0","#27ae60"]
OSRM_BASE  = "https://router.project-osrm.org"
KM_TO_MILES = 0.621371

# ── Supabase ───────────────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def load_data(key):
    cid = st.session_state.get("campaign_id", "default")
    scoped_key = f"{cid}_{key}"
    try:
        res = get_supabase().table("campaign_data").select("data").eq("id", scoped_key).execute()
        return res.data[0]["data"] if res.data else []
    except:
        return []

def save_data(key, value):
    cid = st.session_state.get("campaign_id", "default")
    scoped_key = f"{cid}_{key}"
    try:
        get_supabase().table("campaign_data").upsert({"id": scoped_key, "data": value, "updated_at": "now()"}).execute()
    except Exception as e:
        st.error(f"Failed to save: {e}")

def create_account(campaign_name, email, password):
    try:
        sb = get_supabase()
        existing = sb.table("campaign_accounts").select("id").eq("email", email).execute()
        if existing.data:
            return None, "An account with that email already exists."
        cid = str(uuid.uuid4())
        sb.table("campaign_accounts").insert({
            "id": cid,
            "campaign_name": campaign_name,
            "email": email.lower().strip(),
            "password_hash": hash_password(password),
        }).execute()
        return cid, None
    except Exception as e:
        return None, str(e)

def login_account(email, password):
    try:
        sb = get_supabase()
        res = sb.table("campaign_accounts").select("*").eq("email", email.lower().strip()).execute()
        if not res.data:
            return None, None, "No account found with that email."
        acct = res.data[0]
        if acct["password_hash"] != hash_password(password):
            return None, None, "Incorrect password."
        return acct["id"], acct["campaign_name"], None
    except Exception as e:
        return None, None, str(e)

# ── Auth gate ──────────────────────────────────────────────────────────────────
if "campaign_id" not in st.session_state:
    st.set_page_config(page_title="Campaign Route Optimizer", page_icon="🗺️", layout="centered")
    st.title("🗺️ Campaign Yard Sign Route Optimizer")
    st.caption("Manage volunteers, plan delivery runs, and track every sign on the map.")
    st.divider()

    auth_tab, signup_tab = st.tabs(["🔑 Log In", "✨ Sign Up"])

    with auth_tab:
        st.subheader("Log in to your campaign")
        login_email = st.text_input("Email", key="login_email", placeholder="you@campaign.com")
        login_pw    = st.text_input("Password", key="login_pw", type="password")
        if st.button("Log In", type="primary", use_container_width=True):
            if login_email and login_pw:
                cid, cname, err = login_account(login_email, login_pw)
                if err:
                    st.error(err)
                else:
                    st.session_state.campaign_id   = cid
                    st.session_state.campaign_name = cname
                    st.session_state.logged_in     = True
                    st.rerun()
            else:
                st.warning("Please enter your email and password.")

    with signup_tab:
        st.subheader("Create a new campaign account")
        su_campaign = st.text_input("Campaign name", key="su_campaign", placeholder="Smith for State Senate")
        su_email    = st.text_input("Email", key="su_email", placeholder="you@campaign.com")
        su_pw       = st.text_input("Password", key="su_pw", type="password")
        su_pw2      = st.text_input("Confirm password", key="su_pw2", type="password")
        if st.button("Create Account", type="primary", use_container_width=True):
            if not su_campaign or not su_email or not su_pw:
                st.warning("All fields are required.")
            elif su_pw != su_pw2:
                st.error("Passwords do not match.")
            elif len(su_pw) < 6:
                st.error("Password must be at least 6 characters.")
            else:
                cid, err = create_account(su_campaign, su_email, su_pw)
                if err:
                    st.error(err)
                else:
                    st.session_state.campaign_id   = cid
                    st.session_state.campaign_name = su_campaign
                    st.session_state.logged_in     = True
                    st.success(f"Account created! Welcome, {su_campaign}.")
                    st.rerun()
    st.stop()

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
    # Try Census Bureau first — fast, free, no key, US addresses only
    try:
        url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
        params = {"address": address, "benchmark": "2020", "format": "json"}
        r = requests.get(url, params=params, timeout=5)
        matches = r.json().get("result", {}).get("addressMatches", [])
        if matches:
            coords = matches[0]["coordinates"]
            return coords["y"], coords["x"]
    except:
        pass
    # Fallback to Nominatim if Census fails
    try:
        geolocator = Nominatim(user_agent="campaign_route_optimizer")
        loc = geolocator.geocode(address, timeout=5)
        if loc:
            return loc.latitude, loc.longitude
    except:
        pass
    return None, None

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
    vol = route["volunteer"]; stops = route["stops"]
    miles = route.get("distance_miles", "—")
    cname = st.session_state.get("campaign_name", "the Campaign")
    lines = [f"Hi {vol['name']},",
             f"\nThank you for volunteering to deliver yard signs for {cname}!",
             f"\nYou have {len(stops)} stop{'s' if len(stops)>1 else ''}, covering ~{miles} miles:\n"]
    for i, s in enumerate(stops):
        prev = vol["address"] if i==0 else stops[i-1]["address"]
        note = f" -- Note: {s['note']}" if s.get("note") else ""
        lines += [f"  Stop {i+1}: {s['address']}{note}", f"  Directions: {gmaps_dir(prev, s['address'])}\n"]
    lines += [f"Return home: {vol['address']}", f"\nTotal: ~{miles} miles",
              f"\nThank you!\n{cname} Team"]
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

# ── Load data (scoped per campaign, only once per session) ────────────────────
cid = st.session_state.get("campaign_id", "")
if st.session_state.get("loaded_for") != cid:
    st.session_state.volunteer_roster = load_data("volunteer_roster") or []
    st.session_state.master_addresses = load_data("master_addresses") or []
    st.session_state.run_address_ids  = load_data("run_address_ids")  or []
    st.session_state.completed        = {c["key"]: c for c in (load_data("completed") or [])}
    st.session_state.route_history    = load_data("route_history")    or []
    st.session_state.routes           = st.session_state.route_history[0]["routes"] if st.session_state.route_history else []
    st.session_state.availability     = set()
    st.session_state.proximity_data   = None
    st.session_state.loaded_for       = cid

def get_master_by_id(aid):
    return next((a for a in st.session_state.master_addresses if a["id"] == aid), None)

def add_to_master(entry):
    """Geocode immediately when adding an address so the map is instant."""
    if not entry.get("lat") or not entry.get("lng"):
        lat, lng = geocode_address(entry["address"])
        if lat:
            entry["lat"] = lat
            entry["lng"] = lng
    st.session_state.master_addresses.append(entry)
    save_data("master_addresses", st.session_state.master_addresses)

# ── UI ─────────────────────────────────────────────────────────────────────────
cname = st.session_state.get("campaign_name", "Campaign")
title_col, logout_col = st.columns([8, 1])
with title_col:
    st.title(f"🗺️ {cname} — Yard Sign Route Optimizer")
with logout_col:
    st.write("")
    if st.button("Log Out", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

tab_roster, tab_addresses, tab_run, tab_map, tab_routes, tab_emails = st.tabs([
    "👥 Volunteers", "🗳️ Constituents", "🚐 Delivery Run", "🗺️ Map", "📍 Routes", "📧 Emails & Texts"
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
            c1, c2, c3 = st.columns(3)
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
                if st.button("🗑️ Remove Selected", use_container_width=True):
                    if del_idx != "—":
                        st.session_state.volunteer_roster = [v for v in roster if v["name"] != del_idx]
                        save_data("volunteer_roster", st.session_state.volunteer_roster)
                        st.rerun()
            with c3:
                st.write("")
                if st.button("🗑️ Clear All Volunteers", use_container_width=True, key="clear_all_vols"):
                    st.session_state.volunteer_roster = []
                    save_data("volunteer_roster", [])
                    st.toast("All volunteers cleared!", icon="🗑️")
                    st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ALL ADDRESSES
# ══════════════════════════════════════════════════════════════════════════════
with tab_addresses:
    st.subheader("🗳️ Constituents")
    st.caption("Master list of every constituent. Updated automatically when stops are completed.")

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
                        new_entries = [p for p in parsed if p["address"].lower() not in existing_addrs]
                        if new_entries:
                            prog = st.progress(0, text=f"Geocoding 0/{len(new_entries)}...")
                            for idx, p in enumerate(new_entries):
                                lat, lng = geocode_address(p["address"])
                                if lat: p["lat"] = lat; p["lng"] = lng
                                st.session_state.master_addresses.append(p)
                                prog.progress((idx+1)/len(new_entries), text=f"Geocoding {idx+1}/{len(new_entries)}...")
                            save_data("master_addresses", st.session_state.master_addresses)
                        st.toast(f"✅ Imported {len(new_entries)} new addresses!", icon="📂")
                        st.rerun()
                with c2:
                    if st.button("➕ Also Add to Current Delivery Run"):
                        existing_addrs = {a["address"].lower() for a in st.session_state.master_addresses}
                        new_entries = [p for p in parsed if p["address"].lower() not in existing_addrs]
                        if new_entries:
                            prog = st.progress(0, text=f"Geocoding 0/{len(new_entries)}...")
                            for idx, p in enumerate(new_entries):
                                lat, lng = geocode_address(p["address"])
                                if lat: p["lat"] = lat; p["lng"] = lng
                                st.session_state.master_addresses.append(p)
                                prog.progress((idx+1)/len(new_entries), text=f"Geocoding {idx+1}/{len(new_entries)}...")
                            save_data("master_addresses", st.session_state.master_addresses)
                        for p in parsed:
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
                with st.spinner("Saving..."):
                    add_to_master({"id": str(uuid.uuid4()), "address": full_addr,
                        "contact": na_contact, "phone": na_phone,
                        "email": na_email, "note": na_note, "status": "pending"})
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
        # Download + delete
        dl_col, del_col1, del_col2, del_col3 = st.columns([2, 3, 1, 1])
        with dl_col:
            csv_bytes = all_df.to_csv(index=False).encode()
            st.download_button("⬇️ Export CSV", data=csv_bytes,
                               file_name="c4c_constituents.csv", mime="text/csv")
        with del_col1:
            del_all_sel = st.selectbox("Delete a constituent",
                                       options=["—"] + [a.get("address","") for a in master],
                                       key="del_all_sel")
        with del_col2:
            st.write("")
            if st.button("🗑️ Delete", key="del_all_btn", use_container_width=True):
                if del_all_sel != "—":
                    removed_ids = {a["id"] for a in master if a.get("address") == del_all_sel}
                    st.session_state.master_addresses = [a for a in st.session_state.master_addresses if a.get("address") != del_all_sel]
                    st.session_state.run_address_ids = [rid for rid in st.session_state.run_address_ids if rid not in removed_ids]
                    save_data("master_addresses", st.session_state.master_addresses)
                    save_data("run_address_ids", st.session_state.run_address_ids)
                    st.toast(f"Deleted: {del_all_sel}", icon="🗑️")
                    st.rerun()
        with del_col3:
            st.write("")
            if st.button("🗑️ Clear All", key="clear_all_const", use_container_width=True):
                st.session_state.master_addresses = []
                st.session_state.run_address_ids = []
                save_data("master_addresses", [])
                save_data("run_address_ids", [])
                st.toast("All constituents cleared!", icon="🗑️")
                st.rerun()

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
        pc1, pc2, pc3, pc4 = st.columns(4)
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
        with pc4:
            if st.button("🗑️ Clear All Pending", use_container_width=True, key="clear_pending"):
                pending_ids = {a["id"] for a in pending}
                st.session_state.master_addresses = [a for a in st.session_state.master_addresses if a["id"] not in pending_ids]
                st.session_state.run_address_ids = [rid for rid in st.session_state.run_address_ids if rid not in pending_ids]
                save_data("master_addresses", st.session_state.master_addresses)
                save_data("run_address_ids", st.session_state.run_address_ids)
                st.toast("All pending addresses cleared!", icon="🗑️")
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
        ud1, ud2 = st.columns(2)
        with ud1:
            undo_addr = st.selectbox("Undo delivery", options=["—"] + [a["address"] for a in delivered], key="undo_del_sel")
            if st.button("↩️ Undo", use_container_width=True, key="undo_del_btn"):
                if undo_addr != "—":
                    idx = next((j for j,a in enumerate(st.session_state.master_addresses) if a["address"]==undo_addr), None)
                    if idx is not None:
                        st.session_state.master_addresses[idx]["status"] = "pending"
                        save_data("master_addresses", st.session_state.master_addresses)
                        st.rerun()
        with ud2:
            if st.button("🗑️ Clear All Delivered", use_container_width=True, key="clear_delivered"):
                delivered_ids = {a["id"] for a in delivered}
                st.session_state.master_addresses = [a for a in st.session_state.master_addresses if a["id"] not in delivered_ids]
                save_data("master_addresses", st.session_state.master_addresses)
                st.toast("All delivered addresses cleared!", icon="🗑️")
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — DELIVERY RUN
# ══════════════════════════════════════════════════════════════════════════════
with tab_run:
    st.subheader("🚐 Delivery Run Setup")
    col1, col2 = st.columns(2)

    # ── LEFT: Volunteers ──
    with col1:
        st.markdown("**Who is available today?**")
        roster = st.session_state.volunteer_roster
        named_roster = [v for v in roster if v.get("name")]

        if not named_roster:
            st.info("No volunteers yet — add them in the Volunteers tab.")
        else:
            # Multiselect for volunteers
            available_names = st.multiselect(
                "Select available volunteers",
                options=[v["name"] for v in named_roster],
                default=[v["name"] for v in named_roster if v["name"] in st.session_state.availability],
                key="vol_multiselect",
                placeholder="Click or type a name to select volunteers..."
            )
            st.session_state.availability = set(available_names)

            if available_names:
                st.caption(f"**{len(available_names)} volunteer{'s' if len(available_names)!=1 else ''} selected:**")
                for name in available_names:
                    v = next((x for x in named_roster if x["name"]==name), None)
                    if v:
                        st.markdown(f"✅ **{v['name']}** — {v.get('address','')}")

    # ── RIGHT: Addresses ──
    with col2:
        st.markdown("**Delivery Addresses for this Run**")

        # Build options from master addresses not yet in run
        master = st.session_state.master_addresses
        already_in_run = set(st.session_state.run_address_ids)

        # Multiselect from constituent list
        addr_options = {a["address"]: a["id"] for a in master}
        current_run_addrs = [a["address"] for a in master if a["id"] in already_in_run]

        selected_addrs = st.multiselect(
            "Select from saved constituents",
            options=list(addr_options.keys()),
            default=current_run_addrs,
            key="addr_multiselect",
            placeholder="Type to search and select addresses..."
        )

        # Sync multiselect back to run_address_ids
        new_ids = [addr_options[addr] for addr in selected_addrs if addr in addr_options]
        if new_ids != st.session_state.run_address_ids:
            st.session_state.run_address_ids = new_ids
            save_data("run_address_ids", st.session_state.run_address_ids)

        st.divider()

        # Add a brand new address not yet in the system
        with st.expander("➕ Add new address not in system", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                nr_street = st.text_input("Street*", key="nr_street", placeholder="123 Oak St")
                nr_contact = st.text_input("Contact", key="nr_contact")
                nr_phone = st.text_input("Phone", key="nr_phone")
            with c2:
                nc1, nc2, nc3 = st.columns(3)
                with nc1: nr_city  = st.text_input("City*",  key="nr_city",  placeholder="Baltimore")
                with nc2: nr_state = st.text_input("State*", key="nr_state", placeholder="MD")
                with nc3: nr_zip   = st.text_input("ZIP*",   key="nr_zip",   placeholder="21201")
                nr_note = st.text_input("Note", key="nr_note", placeholder="e.g. leave at side door")
            if st.button("Add to Run + Constituents", type="primary", key="nr_add"):
                if nr_street and nr_city and nr_state and nr_zip:
                    full_addr = f"{nr_street}, {nr_city}, {nr_state} {nr_zip}"
                    e = {"id": str(uuid.uuid4()), "address": full_addr,
                         "contact": nr_contact, "phone": nr_phone,
                         "note": nr_note, "status": "pending"}
                    with st.spinner("Saving..."):
                        add_to_master(e)
                    st.session_state.run_address_ids.append(e["id"])
                    save_data("run_address_ids", st.session_state.run_address_ids)
                    st.toast(f"Added: {full_addr}", icon="📍")
                    st.rerun()
                else:
                    st.warning("Street, city, state, and ZIP are required.")

        # Bulk import
        with st.expander("📋 Bulk import addresses", expanded=False):
            bulk = st.text_area("One address per line:", height=80, key="run_bulk",
                                placeholder="456 Elm Ave, Baltimore, MD\n789 Oak St, Baltimore, MD")
            if st.button("Import", key="run_bulk_btn"):
                lines = [l.strip() for l in bulk.splitlines() if l.strip()]
                prog = st.progress(0, text=f"Geocoding 0/{len(lines)}...")
                for idx, l in enumerate(lines):
                    e = {"id": str(uuid.uuid4()), "address": l,
                         "contact": "", "phone": "", "note": "", "status": "pending"}
                    lat, lng = geocode_address(l)
                    if lat: e["lat"] = lat; e["lng"] = lng
                    st.session_state.master_addresses.append(e)
                    st.session_state.run_address_ids.append(e["id"])
                    prog.progress((idx+1)/len(lines), text=f"Geocoding {idx+1}/{len(lines)}...")
                save_data("master_addresses", st.session_state.master_addresses)
                save_data("run_address_ids", st.session_state.run_address_ids)
                st.toast(f"Imported {len(lines)} addresses", icon="📋")
                st.rerun()

        # Summary of current run
        run_addrs = [get_master_by_id(aid) for aid in st.session_state.run_address_ids]
        run_addrs = [a for a in run_addrs if a]
        if run_addrs:
            st.caption(f"**{len(run_addrs)} stop{'s' if len(run_addrs)!=1 else ''} in this run**")

    st.divider()

    # ── Urgency + action buttons ──
    urg_col, _ = st.columns([3, 1])
    with urg_col:
        urgency = st.radio(
            "**When does this need to get done?**",
            options=["🚗 Today — single trip, optimize full routes", "📅 Sometime soon — show proximity clusters, no routes needed"],
            key="run_urgency",
            horizontal=True
        )

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
        btn_label = "🚀 Optimize Routes" if "Today" in urgency else "🗺️ Show Proximity Map"
        if st.button(btn_label, type="primary", use_container_width=True):
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

            if "Today" in urgency:
                # ── FULL ROUTE OPTIMIZATION ──
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
                st.session_state.proximity_data = None
                run_record = {"timestamp": datetime.now().strftime("%b %d, %Y at %I:%M %p"), "routes": routes}
                st.session_state.route_history = [run_record] + (st.session_state.route_history or [])
                save_data("route_history", st.session_state.route_history)
                st.toast(f"Routes ready — {len(del_results)} stops across {len(routes)} volunteers", icon="🗺️")

            else:
                # ── PROXIMITY CLUSTERING (no routes, just grouping) ──
                # Assign each delivery to nearest volunteer by straight-line distance
                clusters = {i: [] for i in range(len(vol_results))}
                for d in del_results:
                    best = min(range(len(vol_results)),
                               key=lambda vi: haversine((vol_results[vi]["lat"], vol_results[vi]["lng"]),
                                                        (d["lat"], d["lng"])))
                    clusters[best].append(d)

                st.session_state.proximity_data = {
                    "volunteers": vol_results,
                    "clusters": clusters,
                    "timestamp": datetime.now().strftime("%b %d, %Y at %I:%M %p")
                }
                # Build soft routes for emails/texts (no road geometry)
                proximity_routes = []
                for vi, vol in enumerate(vol_results):
                    if not clusters[vi]: continue
                    proximity_routes.append({
                        "volunteer": vol,
                        "stops": clusters[vi],
                        "distance_km": 0,
                        "distance_miles": "—",
                        "road_geometry": None,
                        "color": COLORS[vi%len(COLORS)],
                        "hex": HEX_COLORS[vi%len(HEX_COLORS)],
                    })
                st.session_state.routes = proximity_routes
                st.toast(f"Proximity map ready — {len(del_results)} stops grouped by nearest volunteer", icon="🗺️")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — MAP
# ══════════════════════════════════════════════════════════════════════════════
with tab_map:
    master    = st.session_state.master_addresses
    completed = st.session_state.completed
    active_routes   = st.session_state.get("routes", [])
    proximity_data  = st.session_state.get("proximity_data", None)

    center = [39.2904, -76.6122]
    m = folium.Map(location=center, zoom_start=11, tiles="CartoDB positron")

    if active_routes:
        # ── ACTIVE ROUTE MAP ──
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
                prev    = vol["address"] if i == 0 else r["stops"][i-1]["address"]
                key     = vol["name"] + "_" + str(i)
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
        if st.button("🗺️ Switch to master map view", key="switch_master"):
            st.session_state.routes = []
            st.rerun()

    elif proximity_data:
        # ── PROXIMITY CLUSTER MAP ──
        vols     = proximity_data["volunteers"]
        clusters = proximity_data["clusters"]
        all_lats, all_lngs = [], []

        for vi, vol in enumerate(vols):
            hex_c = HEX_COLORS[vi % len(HEX_COLORS)]
            color = COLORS[vi % len(COLORS)]
            all_lats.append(vol["lat"]); all_lngs.append(vol["lng"])

            # Volunteer home marker
            folium.Marker(
                location=[vol["lat"], vol["lng"]],
                popup=folium.Popup(f"<b>Home: {vol['name']}</b><br>{vol['address']}<br>{len(clusters[vi])} stops nearby", max_width=220),
                tooltip=f"Home: {vol['name']} — {len(clusters[vi])} stops",
                icon=folium.Icon(color=color, icon="home", prefix="fa"),
            ).add_to(m)

            # Each stop as a colored circle matching the volunteer
            for stop in clusters[vi]:
                contact_html = f"<br>👤 {stop['contact']}" if stop.get("contact") else ""
                note_html    = f"<br>📝 {stop['note']}" if stop.get("note") else ""
                all_lats.append(stop["lat"]); all_lngs.append(stop["lng"])
                folium.CircleMarker(
                    location=[stop["lat"], stop["lng"]],
                    radius=10, color=hex_c, fill=True, fill_color=hex_c, fill_opacity=0.7,
                    popup=folium.Popup(f"<b>{stop['address']}</b>{contact_html}{note_html}<br><i>Nearest to {vol['name']}</i>", max_width=230),
                    tooltip=f"{stop['address']} → {vol['name']}",
                ).add_to(m)

            # Draw a light spoke from volunteer home to each stop
            for stop in clusters[vi]:
                folium.PolyLine(
                    [[vol["lat"], vol["lng"]], [stop["lat"], stop["lng"]]],
                    color=hex_c, weight=1.5, opacity=0.3, dash_array="6"
                ).add_to(m)

        if all_lats:
            m.location = [sum(all_lats)/len(all_lats), sum(all_lngs)/len(all_lngs)]
            m.zoom_start = 12

        # Legend
        st.markdown(f"**📅 Proximity clusters — {proximity_data['timestamp']}**")
        leg_cols = st.columns(len(vols) + 1)
        for vi, vol in enumerate(vols):
            leg_cols[vi].markdown(
                f'<span style="color:{HEX_COLORS[vi%len(HEX_COLORS)]};font-size:18px;">&#9679;</span> '
                f'**{vol["name"]}** ({len(clusters[vi])} stops)',
                unsafe_allow_html=True
            )
        leg_cols[-1].write("")
        if st.button("🗺️ Switch to master map view", key="switch_master_prox"):
            st.session_state.proximity_data = None
            st.rerun()

    else:
        # ── DEFAULT MASTER MAP VIEW ──
        geocoded       = [a for a in st.session_state.master_addresses if a.get("lat") and a.get("lng")]
        delivered_pins = [a for a in geocoded if a.get("status") == "delivered"]
        pending_pins   = [a for a in geocoded if a.get("status") != "delivered"]
        ungeocoded     = [a for a in st.session_state.master_addresses if not a.get("lat")]
        if ungeocoded:
            st.caption(f"⚠️ {len(ungeocoded)} address{'es' if len(ungeocoded)!=1 else ''} could not be located on the map.")

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
# TAB 6 — EMAILS & TEXTS
# ══════════════════════════════════════════════════════════════════════════════
with tab_emails:
    if not st.session_state.get("routes"):
        st.info("Run the optimizer first on the Delivery Run tab.")
    else:
        routes = st.session_state.routes
        subj = "Conway for Congress - Your Yard Sign Delivery Route"

        st.subheader("📧 Emails & Texts")

        # ── Email All + Text All buttons ──
        all_emails = [r["volunteer"].get("email","") for r in routes if r["volunteer"].get("email","")]
        all_phones = [r["volunteer"].get("phone","") for r in routes if r["volunteer"].get("phone","")]

        btn_row1, btn_row2 = st.columns(2)
        with btn_row1:
            if all_emails:
                all_bodies   = "\n\n---\n\n".join([generate_email(r) for r in routes])
                email_all_href = mailto_link(",".join(all_emails), subj, all_bodies)
                st.markdown(
                    f'<a href="{email_all_href}" style="display:inline-block;padding:12px 24px;'
                    f'background:#2563eb;color:white;border-radius:8px;text-decoration:none;'
                    f'font-weight:600;font-size:15px;">📧 Email All Volunteers</a>',
                    unsafe_allow_html=True)
                st.caption(f"Opens mail app — {len(all_emails)} recipients")
            else:
                st.warning("No volunteer emails on file.")

        with btn_row2:
            if all_phones:
                # Build a short summary text for all volunteers
                all_text_lines = []
                for r in routes:
                    vp = r["volunteer"].get("phone","").replace("-","").replace(" ","").replace("(","").replace(")","")
                    if not vp: continue
                    stops_preview = ", ".join([s["address"].split(",")[0] for s in r["stops"][:3]])
                    if len(r["stops"]) > 3: stops_preview += f" +{len(r['stops'])-3} more"
                    all_text_lines.append(f"{r['volunteer']['name']}: {stops_preview}")
                bulk_text = (
                    f"Conway for Congress — Delivery run summary:\n" +
                    "\n".join(all_text_lines) +
                    "\nFull route details coming by email!"
                )
                # sms: with comma-separated numbers (works on iOS, opens group thread)
                all_nums = ",".join([
                    r["volunteer"].get("phone","").replace("-","").replace(" ","").replace("(","").replace(")","")
                    for r in routes if r["volunteer"].get("phone","")
                ])
                sms_all_href = f"sms:{all_nums}&body={urllib.parse.quote(bulk_text)}"
                st.markdown(
                    f'<a href="{sms_all_href}" style="display:inline-block;padding:12px 24px;'
                    f'background:#16a34a;color:white;border-radius:8px;text-decoration:none;'
                    f'font-weight:600;font-size:15px;">💬 Text All Volunteers</a>',
                    unsafe_allow_html=True)
                st.caption(f"Opens Messages app — {len(all_phones)} recipients")
            else:
                st.warning("No volunteer phone numbers on file.")

        st.divider()

        # ── Per volunteer ──
        for r in routes:
            vol = r["volunteer"]
            ve   = vol.get("email", "")
            vp   = vol.get("phone", "").replace("-","").replace(" ","").replace("(","").replace(")","")
            body = generate_email(r)

            # Generate short text message
            stops_text = "\n".join([f"  {i+1}. {s['address']}" for i,s in enumerate(r['stops'])])
            text_body = (
                f"Hi {vol['name']}! Conway for Congress here. "
                f"You have {len(r['stops'])} yard sign stop{'s' if len(r['stops'])!=1 else ''} today:\n"
                f"{stops_text}\n"
                f"Full route details coming by email. Thank you!"
            )
            sms_href = f"sms:{vp}{'&' if vp else '?'}body={urllib.parse.quote(text_body)}"

            with st.expander(f"{vol['name']} — {ve or 'no email'} · {vol.get('phone','no phone')}", expanded=True):
                btn_col1, btn_col2 = st.columns(2)

                with btn_col1:
                    st.markdown("**📧 Email**")
                    if ve:
                        st.markdown(
                            f'<a href="{mailto_link(ve, subj, body)}" style="display:inline-block;'
                            f'padding:10px 20px;background:#2563eb;color:white;border-radius:8px;'
                            f'text-decoration:none;font-weight:600;font-size:14px;">Open in Mail App</a>',
                            unsafe_allow_html=True)
                        st.caption(f"To: {ve}")
                    else:
                        st.warning("No email on file.")

                with btn_col2:
                    st.markdown("**💬 Text**")
                    if vp:
                        st.markdown(
                            f'<a href="{sms_href}" style="display:inline-block;'
                            f'padding:10px 20px;background:#16a34a;color:white;border-radius:8px;'
                            f'text-decoration:none;font-weight:600;font-size:14px;">Open in Messages</a>',
                            unsafe_allow_html=True)
                        st.caption(f"To: {vol.get('phone','')}")
                    else:
                        st.warning("No phone on file.")

                st.text_area("Email body (copy manually if needed):", value=body, height=250, key=f"email_{vol['name']}")
