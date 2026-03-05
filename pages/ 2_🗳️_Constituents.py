import streamlit as st
import pandas as pd
import uuid
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.core import page_header, save, add_addr, geocode, col
from datetime import datetime

st.set_page_config(page_title="Constituents", page_icon="🗳️", layout="wide")
page_header("🗳️ Constituents")

addrs     = st.session_state.addrs
pending   = [a for a in addrs if a.get("status") != "delivered"]
delivered = [a for a in addrs if a.get("status") == "delivered"]

# ── CSV import ─────────────────────────────────────────────────────────────────
with st.expander("📂 Import from CSV"):
    cf = st.file_uploader("Upload CSV", type=["csv"], key="ccsv")
    if cf:
        try:
            df = pd.read_csv(cf, dtype=str).fillna("")
            ac=col(df.columns,"addr"); fn=col(df.columns,"first"); ln=col(df.columns,"last")
            ec=col(df.columns,"email"); pc=col(df.columns,"phone")
            cc=col(df.columns,"city"); sc=col(df.columns,"state"); zc=col(df.columns,"zip")
            parsed = []
            for _, row in df.iterrows():
                addr = row[ac].strip() if ac else ""
                if not addr: continue
                parts = [p for p in [
                    row[cc].strip() if cc else "",
                    row[sc].strip() if sc else "",
                    row[zc].strip() if zc else ""
                ] if p]
                if parts: addr += ", " + ", ".join(parts)
                f = row[fn].strip() if fn else ""
                l = row[ln].strip() if ln else ""
                parsed.append({"id": str(uuid.uuid4()), "address": addr,
                    "contact": (f+" "+l).strip(),
                    "phone": row[pc].strip() if pc else "",
                    "email": row[ec].strip() if ec else "",
                    "note": "", "status": "pending"})
            st.success(f"{len(parsed)} addresses found")
            st.dataframe(pd.DataFrame([{"Address": p["address"], "Contact": p["contact"]} for p in parsed]),
                         use_container_width=True, hide_index=True)
            ci1, ci2 = st.columns(2)
            with ci1:
                if st.button("✅ Import to Constituents", type="primary", key="cimp1"):
                    ex = {a["address"].lower() for a in st.session_state.addrs}
                    new = [p for p in parsed if p["address"].lower() not in ex]
                    prog = st.progress(0, text="Geocoding...")
                    for i, p in enumerate(new):
                        lat, lng = geocode(p["address"])
                        if lat: p["lat"] = lat; p["lng"] = lng
                        st.session_state.addrs.append(p)
                        prog.progress((i+1)/max(len(new),1), text=f"Geocoding {i+1}/{len(new)}...")
                    save("addrs", st.session_state.addrs)
                    st.toast(f"Imported {len(new)}!")
                    st.rerun()
            with ci2:
                if st.button("➕ Import + Add to Run", key="cimp2"):
                    ex = {a["address"].lower() for a in st.session_state.addrs}
                    new = [p for p in parsed if p["address"].lower() not in ex]
                    prog = st.progress(0, text="Geocoding...")
                    for i, p in enumerate(new):
                        lat, lng = geocode(p["address"])
                        if lat: p["lat"] = lat; p["lng"] = lng
                        st.session_state.addrs.append(p)
                        prog.progress((i+1)/max(len(new),1))
                    save("addrs", st.session_state.addrs)
                    for p in parsed:
                        e = next((a for a in st.session_state.addrs if a["address"].lower()==p["address"].lower()), None)
                        if e and e["id"] not in st.session_state.run_ids:
                            st.session_state.run_ids.append(e["id"])
                    save("run_ids", st.session_state.run_ids)
                    st.toast("Imported and added to run!")
                    st.rerun()
        except Exception as e:
            st.error(str(e))

# ── Add manually ───────────────────────────────────────────────────────────────
with st.expander("➕ Add manually"):
    m1, m2 = st.columns(2)
    with m1:
        mas  = st.text_input("Street*", key="mas")
        mac  = st.text_input("Contact", key="mac")
        map2 = st.text_input("Phone",   key="map2")
    with m2:
        mc1,mc2,mc3 = st.columns(3)
        with mc1: maci = st.text_input("City*",  key="maci")
        with mc2: mast = st.text_input("State*", key="mast")
        with mc3: mazp = st.text_input("ZIP*",   key="mazp")
        mae = st.text_input("Email", key="mae")
        man = st.text_input("Note",  key="man")
    if st.button("Add Address", type="primary", key="madd"):
        if mas and maci and mast and mazp:
            with st.spinner("Saving..."):
                add_addr({"id": str(uuid.uuid4()),
                    "address": f"{mas}, {maci}, {mast} {mazp}",
                    "contact": mac, "phone": map2,
                    "email": mae, "note": man, "status": "pending"})
            st.toast("Address added!")
            st.rerun()
        else:
            st.warning("Street, city, state, ZIP required.")

st.divider()

# ── All constituents ───────────────────────────────────────────────────────────
st.markdown(f"### 📋 All Constituents — {len(addrs)}")
if addrs:
    adf = pd.DataFrame([{
        "Status":  "✅ Placed" if a.get("status")=="delivered" else "⏳ Pending",
        "Address": a.get("address",""), "Contact": a.get("contact",""),
        "Phone":   a.get("phone",""),   "Date":    a.get("delivered_date",""),
        "Note":    a.get("note","")
    } for a in addrs])
    st.dataframe(adf, use_container_width=True, hide_index=True)
    d1, d2, d3, d4 = st.columns(4)
    with d1:
        st.download_button("⬇️ Export CSV", data=adf.to_csv(index=False).encode(),
                           file_name="constituents.csv", mime="text/csv")
    with d2:
        dsel = st.selectbox("Delete one", options=["—"]+[a["address"] for a in addrs], key="cdel")
    with d3:
        st.write("")
        if st.button("🗑️ Delete", use_container_width=True, key="cdelb"):
            if dsel != "—":
                rids = {a["id"] for a in addrs if a["address"]==dsel}
                st.session_state.addrs    = [a for a in addrs if a["address"]!=dsel]
                st.session_state.run_ids  = [r for r in st.session_state.run_ids if r not in rids]
                save("addrs", st.session_state.addrs)
                save("run_ids", st.session_state.run_ids)
                st.rerun()
    with d4:
        st.write("")
        if st.button("🗑️ Clear All", use_container_width=True, key="ccla"):
            st.session_state.addrs = []; st.session_state.run_ids = []
            save("addrs", []); save("run_ids", [])
            st.rerun()

st.divider()

# ── Pending ────────────────────────────────────────────────────────────────────
st.markdown(f"### ⏳ Pending — {len(pending)}")
if pending:
    pdf = pd.DataFrame([{"Address":a.get("address",""),"Contact":a.get("contact",""),
        "Phone":a.get("phone",""),"Email":a.get("email",""),
        "Note":a.get("note",""),"_id":a["id"]} for a in pending])
    ped = st.data_editor(pdf.drop(columns=["_id"]), use_container_width=True,
                         hide_index=True, key="ped")
    pc1,pc2,pc3,pc4 = st.columns(4)
    with pc1:
        if st.button("💾 Save Edits", use_container_width=True, key="psave"):
            for i, row in ped.iterrows():
                mid = pdf.iloc[i]["_id"]
                ix  = next((j for j,a in enumerate(st.session_state.addrs) if a["id"]==mid), None)
                if ix is not None:
                    st.session_state.addrs[ix].update({
                        "address": row["Address"], "contact": row["Contact"],
                        "phone": row["Phone"], "email": row["Email"], "note": row["Note"]})
            save("addrs", st.session_state.addrs)
            st.toast("Saved!")
    with pc2:
        mds = st.selectbox("Mark delivered", options=["—"]+[a["address"] for a in pending], key="pmds")
        if st.button("✅ Mark Delivered", use_container_width=True, key="pmdb"):
            if mds != "—":
                ix = next((j for j,a in enumerate(st.session_state.addrs) if a["address"]==mds), None)
                if ix is not None:
                    st.session_state.addrs[ix]["status"] = "delivered"
                    st.session_state.addrs[ix]["delivered_date"] = datetime.now().strftime("%b %d, %Y")
                    save("addrs", st.session_state.addrs)
                    st.rerun()
    with pc3:
        rms = st.selectbox("Remove one", options=["—"]+[a["address"] for a in pending], key="prms")
        if st.button("🗑️ Remove", use_container_width=True, key="prmb"):
            if rms != "—":
                st.session_state.addrs = [a for a in st.session_state.addrs if a["address"]!=rms]
                save("addrs", st.session_state.addrs)
                st.rerun()
    with pc4:
        if st.button("🗑️ Clear Pending", use_container_width=True, key="pclb"):
            pids = {a["id"] for a in pending}
            st.session_state.addrs   = [a for a in st.session_state.addrs if a["id"] not in pids]
            st.session_state.run_ids = [r for r in st.session_state.run_ids if r not in pids]
            save("addrs", st.session_state.addrs)
            save("run_ids", st.session_state.run_ids)
            st.rerun()
else:
    st.info("No pending addresses.")

st.divider()

# ── Signs placed ───────────────────────────────────────────────────────────────
st.markdown(f"### ✅ Signs Placed — {len(delivered)}")
if delivered:
    ddf = pd.DataFrame([{"Address":a.get("address",""),"Contact":a.get("contact",""),
        "Date":a.get("delivered_date",""),"Note":a.get("note","")} for a in delivered])
    st.dataframe(ddf, use_container_width=True, hide_index=True)
    u1, u2 = st.columns(2)
    with u1:
        undo = st.selectbox("Undo", options=["—"]+[a["address"] for a in delivered], key="undo")
        if st.button("↩️ Undo Delivery", use_container_width=True, key="undob"):
            if undo != "—":
                ix = next((j for j,a in enumerate(st.session_state.addrs) if a["address"]==undo), None)
                if ix is not None:
                    st.session_state.addrs[ix]["status"] = "pending"
                    save("addrs", st.session_state.addrs)
                    st.rerun()
    with u2:
        if st.button("🗑️ Clear Delivered", use_container_width=True, key="dcla"):
            dids = {a["id"] for a in delivered}
            st.session_state.addrs = [a for a in st.session_state.addrs if a["id"] not in dids]
            save("addrs", st.session_state.addrs)
            st.rerun()
else:
    st.info("No signs placed yet.")
