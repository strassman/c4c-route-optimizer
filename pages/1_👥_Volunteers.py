import streamlit as st
import pandas as pd
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.core import page_header, save, col

st.set_page_config(page_title="Volunteers", page_icon="👥", layout="wide")
page_header("👥 Volunteer Roster")

# ── Add new ────────────────────────────────────────────────────────────────────
with st.container(border=True):
    st.markdown("**Add New Volunteer**")
    a1, a2 = st.columns(2)
    with a1:
        vn  = st.text_input("Full Name*",  key="vn",  placeholder="Jane Smith")
        ve  = st.text_input("Email",       key="ve",  placeholder="jane@email.com")
        vph = st.text_input("Phone",       key="vph", placeholder="410-555-0100")
    with a2:
        vs = st.text_input("Street*", key="vs", placeholder="123 Main St")
        vc1,vc2,vc3 = st.columns(3)
        with vc1: vci = st.text_input("City*",  key="vci", placeholder="Baltimore")
        with vc2: vst = st.text_input("State*", key="vst", placeholder="MD")
        with vc3: vzp = st.text_input("ZIP*",   key="vzp", placeholder="21201")
    if st.button("➕ Add Volunteer", type="primary", key="vadd"):
        if vn and vs and vci and vst and vzp:
            st.session_state.vols.append({
                "name": vn, "email": ve, "phone": vph,
                "address": f"{vs}, {vci}, {vst} {vzp}"
            })
            save("vols", st.session_state.vols)
            st.toast(f"✅ {vn} added!")
            st.rerun()
        else:
            st.warning("Name, street, city, state, ZIP required.")

# ── CSV import ─────────────────────────────────────────────────────────────────
with st.expander("📂 Import volunteers from CSV"):
    vf = st.file_uploader("Upload CSV", type=["csv"], key="vcsv")
    if vf:
        try:
            df = pd.read_csv(vf, dtype=str).fillna("")
            rows = []
            for _, row in df.iterrows():
                fn = row[col(df.columns,"first")].strip() if col(df.columns,"first") else ""
                ln = row[col(df.columns,"last")].strip()  if col(df.columns,"last")  else ""
                name = (fn+" "+ln).strip()
                if not name: continue
                ac=col(df.columns,"addr"); cc=col(df.columns,"city")
                sc=col(df.columns,"state"); zc=col(df.columns,"zip")
                addr = row[ac].strip() if ac else ""
                parts = [p for p in [
                    row[cc].strip() if cc else "",
                    row[sc].strip() if sc else "",
                    row[zc].strip() if zc else ""
                ] if p]
                if parts: addr += ", " + ", ".join(parts)
                ec=col(df.columns,"email"); pc=col(df.columns,"phone")
                rows.append({"name":name,"email":row[ec].strip() if ec else "",
                    "phone":row[pc].strip() if pc else "","address":addr})
            st.success(f"{len(rows)} volunteers found")
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            if st.button("✅ Import Volunteers", type="primary", key="vimp"):
                ex = {v["name"].lower() for v in st.session_state.vols}
                new = [r for r in rows if r["name"].lower() not in ex]
                st.session_state.vols += new
                save("vols", st.session_state.vols)
                st.toast(f"Imported {len(new)} volunteers!")
                st.rerun()
        except Exception as e:
            st.error(str(e))

st.divider()

# ── Roster spreadsheet ─────────────────────────────────────────────────────────
vols = st.session_state.vols
if not vols:
    st.info("No volunteers yet. Add one above or import from CSV.")
else:
    vdf = pd.DataFrame([{
        "Name": v.get("name",""), "Email": v.get("email",""),
        "Phone": v.get("phone",""), "Address": v.get("address","")
    } for v in vols])
    ved = st.data_editor(vdf, use_container_width=True, hide_index=True,
                         num_rows="fixed", key="ved")
    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("💾 Save Changes", use_container_width=True, key="vsave"):
            st.session_state.vols = [{
                "name": r["Name"],"email": r["Email"],
                "phone": r["Phone"],"address": r["Address"]
            } for _,r in ved.iterrows()]
            save("vols", st.session_state.vols)
            st.toast("Roster saved!")
    with b2:
        vdel = st.selectbox("Remove volunteer", options=["—"]+[v["name"] for v in vols], key="vdel")
        if st.button("🗑️ Remove", use_container_width=True, key="vrm"):
            if vdel != "—":
                st.session_state.vols = [v for v in vols if v["name"] != vdel]
                save("vols", st.session_state.vols)
                st.rerun()
    with b3:
        if st.button("🗑️ Clear All Volunteers", use_container_width=True, key="vcla"):
            st.session_state.vols = []
            save("vols", [])
            st.rerun()
