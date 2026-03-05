import streamlit as st
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
import pandas as pd, math, requests, urllib.parse, uuid, hashlib
from datetime import datetime
from supabase import create_client

st.set_page_config(page_title="Campaign Route Optimizer", page_icon="🗺️", layout="wide")

COLORS     = ["red","blue","green","orange","purple","darkred","cadetblue","darkgreen"]
HEX_COLORS = ["#e74c3c","#3498db","#2ecc71","#f39c12","#9b59b6","#c0392b","#5f9ea0","#27ae60"]

# ── DB (cached connection) ─────────────────────────────────────────────────────
@st.cache_resource
def db():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

def hp(pw): return hashlib.sha256(pw.encode()).hexdigest()

def load_all(cid):
    keys = ["vols","addrs","run_ids","done","history"]
    ids  = [f"{cid}_{k}" for k in keys]
    try:
        rows = db().table("campaign_data").select("id,data").in_("id", ids).execute().data
        lk = {r["id"]: r["data"] for r in rows}
        return {k: lk.get(f"{cid}_{k}", []) for k in keys}
    except: return {k: [] for k in keys}

def save(key, val):
    cid = st.session_state.get("cid","")
    try: db().table("campaign_data").upsert({"id":f"{cid}_{key}","data":val}).execute()
    except: pass

# ── Geocoding ──────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=86400*30)
def geocode(addr):
    try:
        r = requests.get("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={"address":addr,"benchmark":"2020","format":"json"}, timeout=5)
        m = r.json().get("result",{}).get("addressMatches",[])
        if m: c=m[0]["coordinates"]; return c["y"],c["x"]
    except: pass
    try:
        loc = Nominatim(user_agent="campaign_opt").geocode(addr, timeout=5)
        if loc: return loc.latitude, loc.longitude
    except: pass
    return None, None

# ── Routing helpers ────────────────────────────────────────────────────────────
def hav(a,b):
    R=6371; la1,lo1,la2,lo2=map(math.radians,[a[0],a[1],b[0],b[1]])
    return R*2*math.asin(math.sqrt(math.sin((la2-la1)/2)**2+math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2))

@st.cache_data(show_spinner=False)
def osrm_matrix(pts):
    try:
        coords=";".join(f"{b},{a}" for a,b in pts)
        r=requests.get(f"https://router.project-osrm.org/table/v1/driving/{coords}?annotations=distance",timeout=20)
        d=r.json()
        if d.get("code")=="Ok": return [[x/1000 for x in row] for row in d["distances"]]
    except: pass
    return [[hav(pts[i],pts[j]) for j in range(len(pts))] for i in range(len(pts))]

@st.cache_data(show_spinner=False)
def osrm_route(wps):
    try:
        coords=";".join(f"{b},{a}" for a,b in wps)
        r=requests.get(f"https://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson",timeout=20)
        d=r.json()
        if d.get("code")=="Ok": return [[p[1],p[0]] for p in d["routes"][0]["geometry"]["coordinates"]]
    except: pass
    return [[a,b] for a,b in wps]

def solve_tsp(fm, hi, stops):
    if not stops: return [],0.0
    n=len(stops); sub=[[fm[stops[i]][stops[j]] for j in range(n)] for i in range(n)]
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
                    if sub[r[i-1]][r[i]]+sub[r[j]][r[(j+1)%n]] > sub[r[i-1]][r[j]]+sub[r[i]][r[(j+1)%n]]+1e-10:
                        r[i:j+1]=r[i:j+1][::-1]; imp=True
        return r
    br,bc=None,1e18
    for s in range(n):
        ro=two_opt(nn(s)); fr=[stops[x] for x in ro]
        cost=fm[hi][fr[0]]+sum(fm[fr[k]][fr[k+1]] for k in range(len(fr)-1))+fm[fr[-1]][hi]
        if cost<bc: bc=cost; br=fr
    return br, round(bc*0.621371,2)

def gmaps(o,d): return f"https://www.google.com/maps/dir/{urllib.parse.quote(o)}/{urllib.parse.quote(d)}"
def mailto(to,sub,body): return f"mailto:{to}?"+urllib.parse.urlencode({"subject":sub,"body":body})

def gen_email(r):
    v=r["volunteer"]; s=r["stops"]; mi=r.get("distance_miles","—")
    cn=st.session_state.get("cname","the Campaign")
    lines=[f"Hi {v['name']},",f"\nThank you for volunteering for {cn}!",
           f"\nYou have {len(s)} stop{'s' if len(s)!=1 else ''} (~{mi} mi):\n"]
    for i,stop in enumerate(s):
        prev=v["address"] if i==0 else s[i-1]["address"]
        lines+=[f"  Stop {i+1}: {stop['address']}",f"  Directions: {gmaps(prev,stop['address'])}\n"]
    lines+=[f"Return home: {v['address']}",f"\nThank you!\n{cn} Team"]
    return "\n".join(lines)

# CSV column detection
FMAP={"addr":["address","street_address","addr","street","address1","mailing_address"],
      "first":["first_name","firstname","fname","first"],"last":["last_name","lastname","lname","last"],
      "email":["email","email_address"],"phone":["phone","phone_number","mobile","cell"],
      "city":["city","town"],"state":["state","state_code"],"zip":["zip","zipcode","zip_code","postal_code"]}
def col(cols,key):
    cl={c.lower().strip().replace(" ","_"):c for c in cols}
    for k in FMAP.get(key,[]): 
        if k in cl: return cl[k]
    return None

# ══════════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════════
if "cid" not in st.session_state:
    st.title("🗺️ Campaign Yard Sign Route Optimizer")
    st.caption("Manage volunteers, plan deliveries, and track every sign on the map.")
    st.divider()
    lt,st2=st.tabs(["🔑 Log In","✨ Sign Up"])
    with lt:
        em=st.text_input("Email",key="li_em"); pw=st.text_input("Password",key="li_pw",type="password")
        if st.button("Log In",type="primary",use_container_width=True):
            if em and pw:
                try:
                    res=db().table("campaign_accounts").select("*").eq("email",em.lower().strip()).execute()
                    if not res.data: st.error("No account found.")
                    elif res.data[0]["password_hash"]!=hp(pw): st.error("Wrong password.")
                    else:
                        a=res.data[0]; st.session_state.cid=a["id"]; st.session_state.cname=a["campaign_name"]; st.rerun()
                except Exception as e: st.error(str(e))
            else: st.warning("Enter email and password.")
    with st2:
        cn=st.text_input("Campaign name",key="su_cn"); em2=st.text_input("Email",key="su_em")
        pw2=st.text_input("Password",key="su_pw",type="password"); pw3=st.text_input("Confirm",key="su_pw2",type="password")
        if st.button("Create Account",type="primary",use_container_width=True):
            if not cn or not em2 or not pw2: st.warning("All fields required.")
            elif pw2!=pw3: st.error("Passwords don't match.")
            elif len(pw2)<6: st.error("Password must be 6+ characters.")
            else:
                try:
                    ex=db().table("campaign_accounts").select("id").eq("email",em2.lower().strip()).execute()
                    if ex.data: st.error("Email already registered.")
                    else:
                        nid=str(uuid.uuid4())
                        db().table("campaign_accounts").insert({"id":nid,"campaign_name":cn,"email":em2.lower().strip(),"password_hash":hp(pw2)}).execute()
                        st.session_state.cid=nid; st.session_state.cname=cn; st.rerun()
                except Exception as e: st.error(str(e))
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# LOAD (single query, once per login)
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.get("loaded_for") != st.session_state.cid:
    d=load_all(st.session_state.cid)
    st.session_state.vols    = d["vols"]    or []
    st.session_state.addrs   = d["addrs"]   or []
    st.session_state.run_ids = d["run_ids"] or []
    st.session_state.done    = {c["key"]:c for c in (d["done"] or [])}
    st.session_state.history = d["history"] or []
    st.session_state.routes  = st.session_state.history[0]["routes"] if st.session_state.history else []
    st.session_state.prox    = None
    st.session_state.avail   = set()
    st.session_state.loaded_for = st.session_state.cid

def by_id(aid): return next((a for a in st.session_state.addrs if a["id"]==aid),None)

def add_addr(e):
    if not e.get("lat"):
        lat,lng=geocode(e["address"])
        if lat: e["lat"]=lat; e["lng"]=lng
    st.session_state.addrs.append(e)
    save("addrs", st.session_state.addrs)

# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
cname=st.session_state.get("cname","Campaign")
hc1,hc2=st.columns([9,1])
with hc1: st.title(f"🗺️ {cname} — Yard Sign Route Optimizer")
with hc2:
    st.write("")
    if st.button("Log Out",use_container_width=True): st.session_state.clear(); st.rerun()

tab_v,tab_c,tab_dr,tab_map,tab_rt,tab_em=st.tabs([
    "👥 Volunteers","🗳️ Constituents","🚐 Delivery Run","🗺️ Map","📍 Routes","📧 Emails & Texts"])

# ══════════════════════════════════════════════════════════════════════════════
# VOLUNTEERS
# ══════════════════════════════════════════════════════════════════════════════
with tab_v:
    st.subheader("👥 Volunteer Roster")
    with st.container(border=True):
        st.markdown("**Add New Volunteer**")
        a1,a2=st.columns(2)
        with a1:
            vn=st.text_input("Full Name*",key="vn",placeholder="Jane Smith")
            ve=st.text_input("Email",key="ve",placeholder="jane@email.com")
            vph=st.text_input("Phone",key="vph",placeholder="410-555-0100")
        with a2:
            vs=st.text_input("Street*",key="vs",placeholder="123 Main St")
            vc1,vc2,vc3=st.columns(3)
            with vc1: vci=st.text_input("City*",key="vci",placeholder="Baltimore")
            with vc2: vst=st.text_input("State*",key="vst",placeholder="MD")
            with vc3: vzp=st.text_input("ZIP*",key="vzp",placeholder="21201")
        if st.button("➕ Add Volunteer",type="primary",key="vadd"):
            if vn and vs and vci and vst and vzp:
                st.session_state.vols.append({"name":vn,"email":ve,"phone":vph,"address":f"{vs}, {vci}, {vst} {vzp}"})
                save("vols",st.session_state.vols); st.toast(f"✅ {vn} added!"); st.rerun()
            else: st.warning("Name, street, city, state, ZIP required.")

    with st.expander("📂 Import volunteers from CSV"):
        vf=st.file_uploader("Upload CSV",type=["csv"],key="vcsv")
        if vf:
            try:
                df=pd.read_csv(vf,dtype=str).fillna("")
                rows=[]
                for _,row in df.iterrows():
                    fn=row[col(df.columns,"first")].strip() if col(df.columns,"first") else ""
                    ln=row[col(df.columns,"last")].strip()  if col(df.columns,"last")  else ""
                    name=(fn+" "+ln).strip()
                    if not name: continue
                    ac=col(df.columns,"addr"); cc=col(df.columns,"city"); sc=col(df.columns,"state"); zc=col(df.columns,"zip")
                    addr=row[ac].strip() if ac else ""
                    parts=[p for p in [row[cc].strip() if cc else "",row[sc].strip() if sc else "",row[zc].strip() if zc else ""] if p]
                    if parts: addr+=", "+", ".join(parts)
                    ec=col(df.columns,"email"); pc=col(df.columns,"phone")
                    rows.append({"name":name,"email":row[ec].strip() if ec else "","phone":row[pc].strip() if pc else "","address":addr})
                st.success(f"{len(rows)} volunteers found")
                st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
                if st.button("✅ Import Volunteers",type="primary",key="vimp"):
                    ex={v["name"].lower() for v in st.session_state.vols}
                    new=[r for r in rows if r["name"].lower() not in ex]
                    st.session_state.vols+=new; save("vols",st.session_state.vols)
                    st.toast(f"Imported {len(new)} volunteers!"); st.rerun()
            except Exception as e: st.error(str(e))

    st.divider()
    vols=st.session_state.vols
    if not vols: st.info("No volunteers yet.")
    else:
        vdf=pd.DataFrame([{"Name":v.get("name",""),"Email":v.get("email",""),"Phone":v.get("phone",""),"Address":v.get("address","")} for v in vols])
        ved=st.data_editor(vdf,use_container_width=True,hide_index=True,num_rows="fixed",key="ved")
        b1,b2,b3=st.columns(3)
        with b1:
            if st.button("💾 Save Changes",use_container_width=True,key="vsave"):
                st.session_state.vols=[{"name":r["Name"],"email":r["Email"],"phone":r["Phone"],"address":r["Address"]} for _,r in ved.iterrows()]
                save("vols",st.session_state.vols); st.toast("Roster saved!")
        with b2:
            vdel=st.selectbox("Remove",options=["—"]+[v["name"] for v in vols],key="vdel")
            if st.button("🗑️ Remove",use_container_width=True,key="vrm"):
                if vdel!="—":
                    st.session_state.vols=[v for v in vols if v["name"]!=vdel]
                    save("vols",st.session_state.vols); st.rerun()
        with b3:
            if st.button("🗑️ Clear All",use_container_width=True,key="vcla"):
                st.session_state.vols=[]; save("vols",[]); st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# CONSTITUENTS
# ══════════════════════════════════════════════════════════════════════════════
with tab_c:
    st.subheader("🗳️ Constituents")
    addrs=st.session_state.addrs
    pending=[a for a in addrs if a.get("status")!="delivered"]
    delivered=[a for a in addrs if a.get("status")=="delivered"]

    with st.expander("📂 Import from CSV"):
        cf=st.file_uploader("Upload CSV",type=["csv"],key="ccsv")
        if cf:
            try:
                df=pd.read_csv(cf,dtype=str).fillna("")
                ac=col(df.columns,"addr"); fn=col(df.columns,"first"); ln=col(df.columns,"last")
                ec=col(df.columns,"email"); pc=col(df.columns,"phone")
                cc=col(df.columns,"city"); sc=col(df.columns,"state"); zc=col(df.columns,"zip")
                parsed=[]
                for _,row in df.iterrows():
                    addr=row[ac].strip() if ac else ""
                    if not addr: continue
                    parts=[p for p in [row[cc].strip() if cc else "",row[sc].strip() if sc else "",row[zc].strip() if zc else ""] if p]
                    if parts: addr+=", "+", ".join(parts)
                    f=row[fn].strip() if fn else ""; l=row[ln].strip() if ln else ""
                    parsed.append({"id":str(uuid.uuid4()),"address":addr,"contact":(f+" "+l).strip(),
                        "phone":row[pc].strip() if pc else "","email":row[ec].strip() if ec else "","note":"","status":"pending"})
                st.success(f"{len(parsed)} addresses found")
                st.dataframe(pd.DataFrame([{"Address":p["address"],"Contact":p["contact"]} for p in parsed]),use_container_width=True,hide_index=True)
                ci1,ci2=st.columns(2)
                with ci1:
                    if st.button("✅ Import to Constituents",type="primary",key="cimp1"):
                        ex={a["address"].lower() for a in addrs}
                        new=[p for p in parsed if p["address"].lower() not in ex]
                        prog=st.progress(0,text="Geocoding...")
                        for i,p in enumerate(new):
                            lat,lng=geocode(p["address"])
                            if lat: p["lat"]=lat; p["lng"]=lng
                            st.session_state.addrs.append(p)
                            prog.progress((i+1)/max(len(new),1),text=f"Geocoding {i+1}/{len(new)}...")
                        save("addrs",st.session_state.addrs); st.toast(f"Imported {len(new)}!"); st.rerun()
                with ci2:
                    if st.button("➕ Import + Add to Run",key="cimp2"):
                        ex={a["address"].lower() for a in addrs}
                        new=[p for p in parsed if p["address"].lower() not in ex]
                        prog=st.progress(0,text="Geocoding...")
                        for i,p in enumerate(new):
                            lat,lng=geocode(p["address"])
                            if lat: p["lat"]=lat; p["lng"]=lng
                            st.session_state.addrs.append(p)
                            prog.progress((i+1)/max(len(new),1))
                        save("addrs",st.session_state.addrs)
                        for p in parsed:
                            e=next((a for a in st.session_state.addrs if a["address"].lower()==p["address"].lower()),None)
                            if e and e["id"] not in st.session_state.run_ids: st.session_state.run_ids.append(e["id"])
                        save("run_ids",st.session_state.run_ids); st.toast("Imported and added to run!"); st.rerun()
            except Exception as e: st.error(str(e))

    with st.expander("➕ Add manually"):
        m1,m2=st.columns(2)
        with m1:
            mas=st.text_input("Street*",key="mas"); mac=st.text_input("Contact",key="mac"); map2=st.text_input("Phone",key="map2")
        with m2:
            mc1,mc2,mc3=st.columns(3)
            with mc1: maci=st.text_input("City*",key="maci")
            with mc2: mast=st.text_input("State*",key="mast")
            with mc3: mazp=st.text_input("ZIP*",key="mazp")
            mae=st.text_input("Email",key="mae"); man=st.text_input("Note",key="man")
        if st.button("Add Address",type="primary",key="madd"):
            if mas and maci and mast and mazp:
                with st.spinner("Saving..."):
                    add_addr({"id":str(uuid.uuid4()),"address":f"{mas}, {maci}, {mast} {mazp}",
                        "contact":mac,"phone":map2,"email":mae,"note":man,"status":"pending"})
                st.toast("Address added!"); st.rerun()
            else: st.warning("Street, city, state, ZIP required.")

    st.divider()
    st.markdown(f"### 📋 All Constituents — {len(addrs)}")
    if addrs:
        adf=pd.DataFrame([{"Status":"✅ Placed" if a.get("status")=="delivered" else "⏳ Pending",
            "Address":a.get("address",""),"Contact":a.get("contact",""),"Phone":a.get("phone",""),
            "Date":a.get("delivered_date",""),"Note":a.get("note","")} for a in addrs])
        st.dataframe(adf,use_container_width=True,hide_index=True)
        d1,d2,d3,d4=st.columns(4)
        with d1:
            st.download_button("⬇️ Export CSV",data=adf.to_csv(index=False).encode(),file_name="constituents.csv",mime="text/csv")
        with d2:
            dsel=st.selectbox("Delete one",options=["—"]+[a["address"] for a in addrs],key="cdel")
        with d3:
            st.write("")
            if st.button("🗑️ Delete",use_container_width=True,key="cdelb"):
                if dsel!="—":
                    rids={a["id"] for a in addrs if a["address"]==dsel}
                    st.session_state.addrs=[a for a in addrs if a["address"]!=dsel]
                    st.session_state.run_ids=[r for r in st.session_state.run_ids if r not in rids]
                    save("addrs",st.session_state.addrs); save("run_ids",st.session_state.run_ids); st.rerun()
        with d4:
            st.write("")
            if st.button("🗑️ Clear All",use_container_width=True,key="ccla"):
                st.session_state.addrs=[]; st.session_state.run_ids=[]
                save("addrs",[]); save("run_ids",[]); st.rerun()

    st.divider()
    st.markdown(f"### ⏳ Pending — {len(pending)}")
    if pending:
        pdf=pd.DataFrame([{"Address":a.get("address",""),"Contact":a.get("contact",""),
            "Phone":a.get("phone",""),"Email":a.get("email",""),"Note":a.get("note",""),"_id":a["id"]} for a in pending])
        ped=st.data_editor(pdf.drop(columns=["_id"]),use_container_width=True,hide_index=True,key="ped")
        pc1,pc2,pc3,pc4=st.columns(4)
        with pc1:
            if st.button("💾 Save Edits",use_container_width=True,key="psave"):
                for i,row in ped.iterrows():
                    mid=pdf.iloc[i]["_id"]
                    ix=next((j for j,a in enumerate(st.session_state.addrs) if a["id"]==mid),None)
                    if ix is not None:
                        st.session_state.addrs[ix].update({"address":row["Address"],"contact":row["Contact"],"phone":row["Phone"],"email":row["Email"],"note":row["Note"]})
                save("addrs",st.session_state.addrs); st.toast("Saved!")
        with pc2:
            mds=st.selectbox("Mark delivered",options=["—"]+[a["address"] for a in pending],key="pmds")
            if st.button("✅ Mark Delivered",use_container_width=True,key="pmdb"):
                if mds!="—":
                    ix=next((j for j,a in enumerate(st.session_state.addrs) if a["address"]==mds),None)
                    if ix is not None:
                        st.session_state.addrs[ix]["status"]="delivered"
                        st.session_state.addrs[ix]["delivered_date"]=datetime.now().strftime("%b %d, %Y")
                        save("addrs",st.session_state.addrs); st.rerun()
        with pc3:
            rms=st.selectbox("Remove one",options=["—"]+[a["address"] for a in pending],key="prms")
            if st.button("🗑️ Remove",use_container_width=True,key="prmb"):
                if rms!="—":
                    st.session_state.addrs=[a for a in st.session_state.addrs if a["address"]!=rms]
                    save("addrs",st.session_state.addrs); st.rerun()
        with pc4:
            if st.button("🗑️ Clear Pending",use_container_width=True,key="pclb"):
                pids={a["id"] for a in pending}
                st.session_state.addrs=[a for a in st.session_state.addrs if a["id"] not in pids]
                st.session_state.run_ids=[r for r in st.session_state.run_ids if r not in pids]
                save("addrs",st.session_state.addrs); save("run_ids",st.session_state.run_ids); st.rerun()

    st.divider()
    st.markdown(f"### ✅ Signs Placed — {len(delivered)}")
    if delivered:
        ddf=pd.DataFrame([{"Address":a.get("address",""),"Contact":a.get("contact",""),
            "Date":a.get("delivered_date",""),"Note":a.get("note","")} for a in delivered])
        st.dataframe(ddf,use_container_width=True,hide_index=True)
        u1,u2=st.columns(2)
        with u1:
            undo=st.selectbox("Undo",options=["—"]+[a["address"] for a in delivered],key="undo")
            if st.button("↩️ Undo Delivery",use_container_width=True,key="undob"):
                if undo!="—":
                    ix=next((j for j,a in enumerate(st.session_state.addrs) if a["address"]==undo),None)
                    if ix is not None:
                        st.session_state.addrs[ix]["status"]="pending"
                        save("addrs",st.session_state.addrs); st.rerun()
        with u2:
            if st.button("🗑️ Clear Delivered",use_container_width=True,key="dcla"):
                dids={a["id"] for a in delivered}
                st.session_state.addrs=[a for a in st.session_state.addrs if a["id"] not in dids]
                save("addrs",st.session_state.addrs); st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# DELIVERY RUN
# ══════════════════════════════════════════════════════════════════════════════
with tab_dr:
    st.subheader("🚐 Delivery Run Setup")
    dc1,dc2=st.columns(2)

    with dc1:
        st.markdown("**Who is available today?**")
        nvols=[v for v in st.session_state.vols if v.get("name")]
        if not nvols: st.info("Add volunteers first.")
        else:
            avsel=st.multiselect("Select available volunteers",
                options=[v["name"] for v in nvols],
                default=[v["name"] for v in nvols if v["name"] in st.session_state.avail],
                placeholder="Click or type to select...",key="avsel")
            st.session_state.avail=set(avsel)
            for name in avsel:
                v=next((x for x in nvols if x["name"]==name),None)
                if v: st.caption(f"✅ {v['name']} — {v.get('address','')}")

    with dc2:
        st.markdown("**Addresses for this Run**")
        aopts={a["address"]:a["id"] for a in st.session_state.addrs}
        cur=[a["address"] for a in st.session_state.addrs if a["id"] in st.session_state.run_ids]
        sel=st.multiselect("Select from constituents",options=list(aopts.keys()),default=cur,
            placeholder="Type to search...",key="addrsel")
        # Update run_ids only when changed (no DB write here — saved on explicit save/optimize)
        new_ids=[aopts[a] for a in sel if a in aopts]
        if new_ids!=st.session_state.run_ids: st.session_state.run_ids=new_ids

        with st.expander("➕ Add new address not in system"):
            nr1,nr2=st.columns(2)
            with nr1:
                nrs=st.text_input("Street*",key="nrs"); nrc=st.text_input("Contact",key="nrc"); nrp=st.text_input("Phone",key="nrp")
            with nr2:
                nr1a,nr2a,nr3a=st.columns(3)
                with nr1a: nrci=st.text_input("City*",key="nrci")
                with nr2a: nrst=st.text_input("State*",key="nrst")
                with nr3a: nrzp=st.text_input("ZIP*",key="nrzp")
                nrn=st.text_input("Note",key="nrn")
            if st.button("Add to Run + Constituents",type="primary",key="nradd"):
                if nrs and nrci and nrst and nrzp:
                    e={"id":str(uuid.uuid4()),"address":f"{nrs}, {nrci}, {nrst} {nrzp}",
                       "contact":nrc,"phone":nrp,"note":nrn,"status":"pending"}
                    with st.spinner("Saving..."): add_addr(e)
                    st.session_state.run_ids.append(e["id"]); save("run_ids",st.session_state.run_ids)
                    st.toast("Added!"); st.rerun()
                else: st.warning("Street, city, state, ZIP required.")
        st.caption(f"**{len(sel)} stop{'s' if len(sel)!=1 else ''} in this run**")

    st.divider()
    urgency=st.radio("**When does this need to get done?**",
        ["🚗 Today — optimize full routes","📅 Sometime soon — show proximity clusters"],
        horizontal=True,key="urgency")

    rb1,rb2,rb3=st.columns(3)
    with rb1:
        if st.button("💾 Save Run",use_container_width=True,key="rsave"):
            save("run_ids",st.session_state.run_ids); st.toast("Run saved!")
    with rb2:
        if st.button("🗑️ Clear Run",use_container_width=True,key="rclr"):
            st.session_state.run_ids=[]; st.session_state.avail=set()
            save("run_ids",[]); st.rerun()
    with rb3:
        btn_label="🚀 Optimize Routes" if "Today" in urgency else "🗺️ Show Proximity Map"
        if st.button(btn_label,type="primary",use_container_width=True,key="ropt"):
            avols=[v for v in st.session_state.vols if v.get("name") and v["name"] in st.session_state.avail]
            raddrs=[by_id(aid) for aid in st.session_state.run_ids]
            raddrs=[a for a in raddrs if a and a.get("address")]
            if not avols: st.error("Select at least one volunteer."); st.stop()
            if not raddrs: st.error("Add at least one address."); st.stop()

            # Geocode volunteers
            with st.spinner("Geocoding volunteers..."):
                vr=[]
                for v in avols:
                    lat,lng=geocode(v["address"])
                    if not lat: st.error(f"Could not geocode volunteer: {v['address']}"); st.stop()
                    vr.append({**v,"lat":lat,"lng":lng})

            # Geocode delivery addresses (use cached lat/lng if available)
            with st.spinner("Geocoding delivery addresses..."):
                dr=[]
                addr_updated=False
                for a in raddrs:
                    lat,lng=a.get("lat"),a.get("lng")
                    if not lat:
                        lat,lng=geocode(a["address"])
                        if lat:
                            ix=next((j for j,x in enumerate(st.session_state.addrs) if x["id"]==a["id"]),None)
                            if ix is not None:
                                st.session_state.addrs[ix]["lat"]=lat
                                st.session_state.addrs[ix]["lng"]=lng
                                addr_updated=True
                    if lat: dr.append({**a,"lat":lat,"lng":lng})
                    else: st.warning(f"Skipping (could not geocode): {a['address']}")
                if addr_updated: save("addrs",st.session_state.addrs)
                if not dr: st.error("No addresses could be geocoded."); st.stop()

            if "Today" in urgency:
                with st.spinner("Optimizing routes..."):
                    pts=tuple((v["lat"],v["lng"]) for v in vr)+tuple((d["lat"],d["lng"]) for d in dr)
                    nv=len(vr); fm=osrm_matrix(pts)
                    clusters={i:[] for i in range(nv)}
                    for di in range(len(dr)):
                        bv=min(range(nv),key=lambda vi:fm[vi][nv+di])
                        clusters[bv].append(nv+di)
                    routes=[]
                    for vi,vol in enumerate(vr):
                        if not clusters[vi]: continue
                        oi,mi=solve_tsp(fm,vi,clusters[vi])
                        stops=[dr[idx-nv] for idx in oi]
                        wps=tuple([(vol["lat"],vol["lng"])]+[(s["lat"],s["lng"]) for s in stops]+[(vol["lat"],vol["lng"])])
                        routes.append({"volunteer":vol,"stops":stops,"distance_miles":mi,
                            "road_geometry":osrm_route(wps),"color":COLORS[vi%8],"hex":HEX_COLORS[vi%8]})
                st.session_state.routes=routes; st.session_state.prox=None
                rec={"timestamp":datetime.now().strftime("%b %d, %Y at %I:%M %p"),"routes":routes}
                st.session_state.history=[rec]+st.session_state.history
                save("history",st.session_state.history)
                st.toast(f"✅ Routes ready — {len(dr)} stops across {len(routes)} volunteers")
            else:
                clusters={i:[] for i in range(len(vr))}
                for a in dr:
                    bv=min(range(len(vr)),key=lambda vi:hav((vr[vi]["lat"],vr[vi]["lng"]),(a["lat"],a["lng"])))
                    clusters[bv].append(a)
                st.session_state.prox={"vols":vr,"clusters":clusters,"ts":datetime.now().strftime("%b %d, %Y at %I:%M %p")}
                st.session_state.routes=[{"volunteer":vr[vi],"stops":clusters[vi],"distance_miles":"—",
                    "road_geometry":None,"color":COLORS[vi%8],"hex":HEX_COLORS[vi%8]} for vi in range(len(vr)) if clusters[vi]]
                st.toast(f"✅ Proximity map ready — {len(dr)} stops grouped")

# ══════════════════════════════════════════════════════════════════════════════
# MAP
# ══════════════════════════════════════════════════════════════════════════════
with tab_map:
    routes=st.session_state.get("routes",[])
    prox=st.session_state.get("prox",None)
    if not routes and not prox and not st.session_state.get("map_open"):
        st.info("Open the map to see all sign placements and pending addresses.")
        if st.button("🗺️ Open Map",type="primary",key="mopen"): st.session_state.map_open=True; st.rerun()
    else:
        done=st.session_state.done
        m=folium.Map(location=[39.2904,-76.6122],zoom_start=11,tiles="CartoDB positron")
        lats,lngs=[],[]

        if routes and not prox:
            for r in routes:
                v=r["volunteer"]; hx=r["hex"]; col_=r["color"]
                if v.get("lat"): lats.append(v["lat"]); lngs.append(v["lng"])
                folium.Marker([v["lat"],v["lng"]],tooltip=f"🏠 {v['name']}",
                    icon=folium.Icon(color=col_,icon="home",prefix="fa")).add_to(m)
                if r.get("road_geometry"):
                    folium.PolyLine(r["road_geometry"],color=hx,weight=4,opacity=0.8).add_to(m)
                for i,s in enumerate(r["stops"]):
                    if not s.get("lat"): continue
                    lats.append(s["lat"]); lngs.append(s["lng"])
                    k=v["name"]+"_"+str(i); is_done=k in done
                    prev=v["address"] if i==0 else r["stops"][i-1]["address"]
                    if is_done:
                        folium.Marker([s["lat"],s["lng"]],tooltip=f"✅ {s['address']}",
                            icon=folium.Icon(color="green",icon="check",prefix="fa")).add_to(m)
                    else:
                        folium.Marker([s["lat"],s["lng"]],
                            popup=folium.Popup(f"<b>Stop {i+1}: {v['name']}</b><br>{s['address']}<br><a href='{gmaps(prev,s['address'])}' target='_blank'>Directions</a>",max_width=250),
                            tooltip=f"Stop {i+1} — {v['name']}",
                            icon=folium.DivIcon(html=f'<div style="background:white;color:{hx};border:2px solid {hx};border-radius:50%;width:26px;height:26px;display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:12px">{i+1}</div>',
                                icon_size=(26,26),icon_anchor=(13,13))).add_to(m)
            lc1,lc2,lc3,_=st.columns([1,1,1.5,4])
            lc1.markdown('<span style="color:#27ae60;font-size:16px;">&#9679;</span> Placed',unsafe_allow_html=True)
            lc2.markdown('<span style="color:#aaa;font-size:16px;">&#9679;</span> Pending',unsafe_allow_html=True)
            if st.button("↩️ Master map",key="mback"): st.session_state.routes=[]; st.rerun()

        elif prox:
            vr=prox["vols"]; cl=prox["clusters"]
            for vi,vol in enumerate(vr):
                hx=HEX_COLORS[vi%8]; col_=COLORS[vi%8]
                if vol.get("lat"): lats.append(vol["lat"]); lngs.append(vol["lng"])
                folium.Marker([vol["lat"],vol["lng"]],tooltip=f"🏠 {vol['name']} ({len(cl[vi])} stops)",
                    icon=folium.Icon(color=col_,icon="home",prefix="fa")).add_to(m)
                for s in cl[vi]:
                    if not s.get("lat"): continue
                    lats.append(s["lat"]); lngs.append(s["lng"])
                    folium.CircleMarker([s["lat"],s["lng"]],radius=9,color=hx,fill=True,fill_color=hx,fill_opacity=0.7,
                        tooltip=f"{s['address']} → {vol['name']}").add_to(m)
                    folium.PolyLine([[vol["lat"],vol["lng"]],[s["lat"],s["lng"]]],
                        color=hx,weight=1.5,opacity=0.3,dash_array="6").add_to(m)
            st.markdown(f"**Proximity clusters — {prox['ts']}**")
            lcols=st.columns(min(len(vr),6))
            for vi,vol in enumerate(vr[:6]):
                lcols[vi].markdown(f'<span style="color:{HEX_COLORS[vi%8]};font-size:16px;">&#9679;</span> {vol["name"]} ({len(cl[vi])})',unsafe_allow_html=True)
            if st.button("↩️ Master map",key="mback2"): st.session_state.prox=None; st.rerun()

        else:
            geocoded=[a for a in st.session_state.addrs if a.get("lat")]
            for a in geocoded:
                lats.append(a["lat"]); lngs.append(a["lng"])
                if a.get("status")=="delivered":
                    folium.Marker([a["lat"],a["lng"]],tooltip=f"✅ {a['address']}",
                        popup=a.get("delivered_date",""),
                        icon=folium.Icon(color="green",icon="check",prefix="fa")).add_to(m)
                else:
                    folium.CircleMarker([a["lat"],a["lng"]],radius=7,color="#888",fill=True,fill_color="#bbb",fill_opacity=0.85,
                        tooltip=f"⏳ {a['address']}").add_to(m)
            lc1,lc2,_=st.columns([1,1,6])
            lc1.markdown('<span style="color:#27ae60;font-size:16px;">&#9679;</span> Placed',unsafe_allow_html=True)
            lc2.markdown('<span style="color:#888;font-size:16px;">&#9679;</span> Pending',unsafe_allow_html=True)
            if not st.session_state.addrs: st.info("No addresses yet.")

        if lats: m.location=[sum(lats)/len(lats),sum(lngs)/len(lngs)]; m.zoom_start=12
        st_folium(m,use_container_width=True,height=560)

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════
with tab_rt:
    history=st.session_state.history
    if not history: st.info("No routes yet. Run the optimizer on the Delivery Run tab.")
    else:
        done=st.session_state.done
        for ri,rec in enumerate(history):
            rh1,rh2=st.columns([7,2])
            with rh1:
                st.markdown(f"### 📅 {rec.get('timestamp','Unknown')}")
                st.caption("🟢 Most recent" if ri==0 else f"Run #{len(history)-ri}")
            with rh2:
                ck=f"confirm_del_{ri}"
                if st.session_state.get(ck):
                    cy,cn2=st.columns(2)
                    with cy:
                        if st.button("✅ Yes",key=f"yes_{ri}",type="primary"):
                            history.pop(ri)
                            save("history",history)
                            st.session_state.routes=history[0]["routes"] if history else []
                            st.session_state[ck]=False; st.rerun()
                    with cn2:
                        if st.button("Cancel",key=f"no_{ri}"):
                            st.session_state[ck]=False; st.rerun()
                else:
                    if st.button("🗑️ Delete Run",key=f"del_{ri}",use_container_width=True):
                        st.session_state[ck]=True; st.rerun()

            for r in rec["routes"]:
                v=r["volunteer"]
                with st.expander(f"**{v['name']}** — {len(r['stops'])} stops · {r.get('distance_miles','—')} mi",expanded=(ri==0)):
                    for i,s in enumerate(r["stops"]):
                        prev=v["address"] if i==0 else r["stops"][i-1]["address"]
                        k=v["name"]+"_"+str(i); is_done=k in done
                        kc,ki=st.columns([1,11])
                        with kc:
                            chk=st.checkbox("",value=is_done,key=f"c_{ri}_{k}")
                            if chk and not is_done:
                                done[k]={"key":k,"address":s["address"],"lat":s.get("lat"),"lng":s.get("lng"),
                                    "volunteer":v["name"],"stop_num":i+1,"delivered_date":datetime.now().strftime("%b %d, %Y")}
                                st.session_state.done=done; save("done",list(done.values()))
                                ix=next((j for j,a in enumerate(st.session_state.addrs) if a.get("id")==s.get("id")),None)
                                if ix is not None:
                                    st.session_state.addrs[ix]["status"]="delivered"
                                    st.session_state.addrs[ix]["delivered_date"]=datetime.now().strftime("%b %d, %Y")
                                    save("addrs",st.session_state.addrs)
                                st.rerun()
                            elif not chk and is_done:
                                del st.session_state.done[k]; save("done",list(st.session_state.done.values()))
                                ix=next((j for j,a in enumerate(st.session_state.addrs) if a.get("id")==s.get("id")),None)
                                if ix is not None:
                                    st.session_state.addrs[ix]["status"]="pending"
                                    save("addrs",st.session_state.addrs)
                                st.rerun()
                        with ki:
                            ct=f" · 👤 {s['contact']}" if s.get("contact") else ""
                            nt=f" — *{s['note']}*" if s.get("note") else ""
                            if is_done:
                                dd=done.get(k,{}).get("delivered_date","")
                                st.markdown(f"~~**Stop {i+1}:** {s['address']}~~ ✅{ct}{nt}"+(f" · {dd}" if dd else ""))
                            else:
                                st.markdown(f"**Stop {i+1}:** {s['address']}{ct}{nt}")
                            st.markdown(f"[Directions]({gmaps(prev,s['address'])})")
            st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# EMAILS & TEXTS
# ══════════════════════════════════════════════════════════════════════════════
with tab_em:
    routes=st.session_state.get("routes",[])
    if not routes: st.info("Run the optimizer first.")
    else:
        subj=f"{cname} - Your Yard Sign Delivery Route"
        all_em=[r["volunteer"].get("email","") for r in routes if r["volunteer"].get("email")]
        all_ph=[r["volunteer"].get("phone","") for r in routes if r["volunteer"].get("phone")]

        ec,tc=st.columns(2)
        with ec:
            if all_em:
                bodies="\n\n---\n\n".join([gen_email(r) for r in routes])
                st.markdown(f'<a href="{mailto(",".join(all_em),subj,bodies)}" style="display:inline-block;padding:12px 24px;background:#2563eb;color:white;border-radius:8px;text-decoration:none;font-weight:600;font-size:15px;">📧 Email All Volunteers</a>',unsafe_allow_html=True)
                st.caption(f"{len(all_em)} recipients")
            else: st.warning("No volunteer emails on file.")
        with tc:
            if all_ph:
                nums=",".join([r["volunteer"]["phone"].translate(str.maketrans("","","- ()")) for r in routes if r["volunteer"].get("phone")])
                lines="\n".join([f"{r['volunteer']['name']}: "+", ".join([s['address'].split(',')[0] for s in r['stops'][:3]])+("…" if len(r['stops'])>3 else "") for r in routes])
                txt=f"{cname} delivery run:\n{lines}\nFull details by email. Thank you!"
                st.markdown(f'<a href="sms:{nums}&body={urllib.parse.quote(txt)}" style="display:inline-block;padding:12px 24px;background:#16a34a;color:white;border-radius:8px;text-decoration:none;font-weight:600;font-size:15px;">💬 Text All Volunteers</a>',unsafe_allow_html=True)
                st.caption(f"{len(all_ph)} recipients")
            else: st.warning("No volunteer phones on file.")

        st.divider()
        for r in routes:
            v=r["volunteer"]; ve=v.get("email",""); vp=v.get("phone","").translate(str.maketrans("","","- ()"))
            body=gen_email(r)
            stops_txt="\n".join([f"  {i+1}. {s['address'].split(',')[0]}" for i,s in enumerate(r["stops"])])
            txt=f"Hi {v['name']}! {cname} here. {len(r['stops'])} stop{'s' if len(r['stops'])!=1 else ''} today:\n{stops_txt}\nFull route by email!"
            with st.expander(f"{v['name']} — {ve or 'no email'} · {v.get('phone','no phone')}",expanded=True):
                bc1,bc2=st.columns(2)
                with bc1:
                    st.markdown("**📧 Email**")
                    if ve:
                        st.markdown(f'<a href="{mailto(ve,subj,body)}" style="display:inline-block;padding:10px 20px;background:#2563eb;color:white;border-radius:8px;text-decoration:none;font-weight:600;">Open in Mail</a>',unsafe_allow_html=True)
                    else: st.warning("No email.")
                with bc2:
                    st.markdown("**💬 Text**")
                    if vp:
                        st.markdown(f'<a href="sms:{vp}&body={urllib.parse.quote(txt)}" style="display:inline-block;padding:10px 20px;background:#16a34a;color:white;border-radius:8px;text-decoration:none;font-weight:600;">Open in Messages</a>',unsafe_allow_html=True)
                    else: st.warning("No phone.")
                st.text_area("Email body:",value=body,height=200,key=f"eb_{v['name']}")
