import streamlit as st
import math, requests, urllib.parse, uuid, hashlib
from datetime import datetime
from geopy.geocoders import Nominatim
from supabase import create_client

COLORS     = ["red","blue","green","orange","purple","darkred","cadetblue","darkgreen"]
HEX_COLORS = ["#e74c3c","#3498db","#2ecc71","#f39c12","#9b59b6","#c0392b","#5f9ea0","#27ae60"]

# ── DB ─────────────────────────────────────────────────────────────────────────
@st.cache_resource
def db():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

def hp(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def load_all(cid):
    keys = ["vols","addrs","run_ids","done","history"]
    ids  = [f"{cid}_{k}" for k in keys]
    try:
        rows = db().table("campaign_data").select("id,data").in_("id", ids).execute().data
        lk   = {r["id"]: r["data"] for r in rows}
        return {k: lk.get(f"{cid}_{k}", []) for k in keys}
    except:
        return {k: [] for k in keys}

def save(key, val):
    cid = st.session_state.get("cid", "")
    try:
        db().table("campaign_data").upsert({"id": f"{cid}_{key}", "data": val}).execute()
    except:
        pass

# ── Auth ───────────────────────────────────────────────────────────────────────
def login(email, pw):
    try:
        res = db().table("campaign_accounts").select("*").eq("email", email.lower().strip()).execute()
        if not res.data: return None, None, "No account found."
        a = res.data[0]
        if a["password_hash"] != hp(pw): return None, None, "Wrong password."
        return a["id"], a["campaign_name"], None
    except Exception as e:
        return None, None, str(e)

def signup(cname, email, pw):
    try:
        ex = db().table("campaign_accounts").select("id").eq("email", email.lower().strip()).execute()
        if ex.data: return None, "Email already registered."
        nid = str(uuid.uuid4())
        db().table("campaign_accounts").insert({
            "id": nid, "campaign_name": cname,
            "email": email.lower().strip(), "password_hash": hp(pw)
        }).execute()
        return nid, None
    except Exception as e:
        return None, str(e)

# ── Session bootstrap ──────────────────────────────────────────────────────────
def require_auth():
    """Call at top of every page. Redirects to login if not authenticated."""
    if "cid" not in st.session_state:
        st.warning("Please log in first.")
        st.page_link("app.py", label="Go to Login", icon="🔑")
        st.stop()
    cid = st.session_state.cid
    if st.session_state.get("loaded_for") != cid:
        d = load_all(cid)
        st.session_state.vols    = d["vols"]    or []
        st.session_state.addrs   = d["addrs"]   or []
        st.session_state.run_ids = d["run_ids"] or []
        st.session_state.done    = {c["key"]: c for c in (d["done"] or [])}
        st.session_state.history = d["history"] or []
        st.session_state.routes  = st.session_state.history[0]["routes"] if st.session_state.history else []
        st.session_state.prox    = None
        st.session_state.avail   = set()
        st.session_state.loaded_for = cid

def page_header(title):
    """Standard header with campaign name and logout button."""
    require_auth()
    cname = st.session_state.get("cname", "Campaign")
    h1, h2 = st.columns([9, 1])
    with h1:
        st.title(f"{title}")
        st.caption(f"Campaign: {cname}")
    with h2:
        st.write("")
        if st.button("Log Out", use_container_width=True):
            st.session_state.clear()
            st.rerun()

# ── Geocoding ──────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=86400*30)
def geocode(addr):
    try:
        r = requests.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={"address": addr, "benchmark": "2020", "format": "json"}, timeout=5)
        m = r.json().get("result", {}).get("addressMatches", [])
        if m:
            c = m[0]["coordinates"]
            return c["y"], c["x"]
    except:
        pass
    try:
        loc = Nominatim(user_agent="campaign_opt").geocode(addr, timeout=5)
        if loc: return loc.latitude, loc.longitude
    except:
        pass
    return None, None

def add_addr(e):
    if not e.get("lat"):
        lat, lng = geocode(e["address"])
        if lat: e["lat"] = lat; e["lng"] = lng
    st.session_state.addrs.append(e)
    save("addrs", st.session_state.addrs)

def by_id(aid):
    return next((a for a in st.session_state.addrs if a["id"] == aid), None)

# ── Routing ────────────────────────────────────────────────────────────────────
def hav(a, b):
    R = 6371
    la1,lo1,la2,lo2 = map(math.radians,[a[0],a[1],b[0],b[1]])
    return R*2*math.asin(math.sqrt(
        math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2))

@st.cache_data(show_spinner=False)
def osrm_matrix(pts):
    try:
        coords = ";".join(f"{b},{a}" for a,b in pts)
        r = requests.get(
            f"https://router.project-osrm.org/table/v1/driving/{coords}?annotations=distance",
            timeout=20)
        d = r.json()
        if d.get("code") == "Ok":
            return [[x/1000 for x in row] for row in d["distances"]]
    except:
        pass
    return [[hav(pts[i],pts[j]) for j in range(len(pts))] for i in range(len(pts))]

@st.cache_data(show_spinner=False)
def osrm_route(wps):
    try:
        coords = ";".join(f"{b},{a}" for a,b in wps)
        r = requests.get(
            f"https://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson",
            timeout=20)
        d = r.json()
        if d.get("code") == "Ok":
            return [[p[1],p[0]] for p in d["routes"][0]["geometry"]["coordinates"]]
    except:
        pass
    return [[a,b] for a,b in wps]

def solve_tsp(fm, hi, stops):
    if not stops: return [], 0.0
    n = len(stops)
    sub = [[fm[stops[i]][stops[j]] for j in range(n)] for i in range(n)]
    def nn(s):
        vis=[False]*n; r=[s]; vis[s]=True
        for _ in range(n-1):
            last=r[-1]; bj,bd=-1,1e18
            for j in range(n):
                if not vis[j] and sub[last][j]<bd: bd=sub[last][j]; bj=j
            r.append(bj); vis[bj]=True
        return r
    def two_opt(r):
        imp=True
        while imp:
            imp=False
            for i in range(1,n-1):
                for j in range(i+1,n):
                    if sub[r[i-1]][r[i]]+sub[r[j]][r[(j+1)%n]]>sub[r[i-1]][r[j]]+sub[r[i]][r[(j+1)%n]]+1e-10:
                        r[i:j+1]=r[i:j+1][::-1]; imp=True
        return r
    br,bc = None,1e18
    for s in range(n):
        ro = two_opt(nn(s)); fr = [stops[x] for x in ro]
        cost = fm[hi][fr[0]]+sum(fm[fr[k]][fr[k+1]] for k in range(len(fr)-1))+fm[fr[-1]][hi]
        if cost < bc: bc=cost; br=fr
    return br, round(bc*0.621371, 2)

# ── Email / SMS helpers ────────────────────────────────────────────────────────
def gmaps(o, d):
    return f"https://www.google.com/maps/dir/{urllib.parse.quote(o)}/{urllib.parse.quote(d)}"

def mailto(to, sub, body):
    return f"mailto:{to}?" + urllib.parse.urlencode({"subject": sub, "body": body})

def gen_email(r):
    v = r["volunteer"]; s = r["stops"]; mi = r.get("distance_miles","—")
    cn = st.session_state.get("cname","the Campaign")
    lines = [f"Hi {v['name']},", f"\nThank you for volunteering for {cn}!",
             f"\nYou have {len(s)} stop{'s' if len(s)!=1 else ''} (~{mi} mi):\n"]
    for i,stop in enumerate(s):
        prev = v["address"] if i==0 else s[i-1]["address"]
        lines += [f"  Stop {i+1}: {stop['address']}", f"  Directions: {gmaps(prev,stop['address'])}\n"]
    lines += [f"Return home: {v['address']}", f"\nThank you!\n{cn} Team"]
    return "\n".join(lines)

# ── CSV helpers ────────────────────────────────────────────────────────────────
FMAP = {
    "addr":  ["address","street_address","addr","street","address1","mailing_address"],
    "first": ["first_name","firstname","fname","first"],
    "last":  ["last_name","lastname","lname","last"],
    "email": ["email","email_address"],
    "phone": ["phone","phone_number","mobile","cell"],
    "city":  ["city","town"],
    "state": ["state","state_code"],
    "zip":   ["zip","zipcode","zip_code","postal_code"],
}

def col(cols, key):
    cl = {c.lower().strip().replace(" ","_"): c for c in cols}
    for k in FMAP.get(key, []):
        if k in cl: return cl[k]
    return None
