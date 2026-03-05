import streamlit as st
import uuid
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.core import (page_header, save, add_addr, by_id, geocode,
                        osrm_matrix, osrm_route, solve_tsp, hav,
                        COLORS, HEX_COLORS)
from datetime import datetime

st.set_page_config(page_title="Delivery Run", page_icon="🚐", layout="wide")
page_header("🚐 Delivery Run Setup")

dc1, dc2 = st.columns(2)

# ── Volunteers ─────────────────────────────────────────────────────────────────
with dc1:
    st.markdown("**Who is available today?**")
    nvols = [v for v in st.session_state.vols if v.get("name")]
    if not nvols:
        st.info("Add volunteers first.")
    else:
        avsel = st.multiselect("Select available volunteers",
            options=[v["name"] for v in nvols],
            default=[v["name"] for v in nvols if v["name"] in st.session_state.avail],
            placeholder="Click or type to select...", key="avsel")
        st.session_state.avail = set(avsel)
        for name in avsel:
            v = next((x for x in nvols if x["name"]==name), None)
            if v: st.caption(f"✅ {v['name']} — {v.get('address','')}")

# ── Addresses ──────────────────────────────────────────────────────────────────
with dc2:
    st.markdown("**Addresses for this Run**")
    aopts = {a["address"]: a["id"] for a in st.session_state.addrs}
    cur   = [a["address"] for a in st.session_state.addrs if a["id"] in st.session_state.run_ids]
    sel   = st.multiselect("Select from constituents", options=list(aopts.keys()),
                default=cur, placeholder="Type to search...", key="addrsel")
    new_ids = [aopts[a] for a in sel if a in aopts]
    if new_ids != st.session_state.run_ids:
        st.session_state.run_ids = new_ids

    with st.expander("➕ Add new address not in system"):
        nr1, nr2 = st.columns(2)
        with nr1:
            nrs = st.text_input("Street*", key="nrs")
            nrc = st.text_input("Contact", key="nrc")
            nrp = st.text_input("Phone",   key="nrp")
        with nr2:
            nr1a,nr2a,nr3a = st.columns(3)
            with nr1a: nrci = st.text_input("City*",  key="nrci")
            with nr2a: nrst = st.text_input("State*", key="nrst")
            with nr3a: nrzp = st.text_input("ZIP*",   key="nrzp")
            nrn = st.text_input("Note", key="nrn")
        if st.button("Add to Run + Constituents", type="primary", key="nradd"):
            if nrs and nrci and nrst and nrzp:
                e = {"id": str(uuid.uuid4()),
                     "address": f"{nrs}, {nrci}, {nrst} {nrzp}",
                     "contact": nrc, "phone": nrp, "note": nrn, "status": "pending"}
                with st.spinner("Saving..."):
                    add_addr(e)
                st.session_state.run_ids.append(e["id"])
                save("run_ids", st.session_state.run_ids)
                st.toast("Added!")
                st.rerun()
            else:
                st.warning("Street, city, state, ZIP required.")

    st.caption(f"**{len(sel)} stop{'s' if len(sel)!=1 else ''} in this run**")

st.divider()

# ── Urgency + action buttons ───────────────────────────────────────────────────
urgency = st.radio("**When does this need to get done?**",
    ["🚗 Today — optimize full routes", "📅 Sometime soon — show proximity clusters"],
    horizontal=True, key="urgency")

rb1, rb2, rb3 = st.columns(3)
with rb1:
    if st.button("💾 Save Run", use_container_width=True, key="rsave"):
        save("run_ids", st.session_state.run_ids)
        st.toast("Run saved!")
with rb2:
    if st.button("🗑️ Clear Run", use_container_width=True, key="rclr"):
        st.session_state.run_ids = []; st.session_state.avail = set()
        save("run_ids", [])
        st.rerun()
with rb3:
    btn_label = "🚀 Optimize Routes" if "Today" in urgency else "🗺️ Show Proximity Map"
    if st.button(btn_label, type="primary", use_container_width=True, key="ropt"):
        avols  = [v for v in st.session_state.vols if v.get("name") and v["name"] in st.session_state.avail]
        raddrs = [by_id(aid) for aid in st.session_state.run_ids]
        raddrs = [a for a in raddrs if a and a.get("address")]
        if not avols:  st.error("Select at least one volunteer."); st.stop()
        if not raddrs: st.error("Add at least one address."); st.stop()

        with st.spinner("Geocoding volunteers..."):
            vr = []
            for v in avols:
                lat, lng = geocode(v["address"])
                if not lat: st.error(f"Could not geocode: {v['address']}"); st.stop()
                vr.append({**v, "lat": lat, "lng": lng})

        with st.spinner("Geocoding delivery addresses..."):
            dr = []; updated = False
            for a in raddrs:
                lat, lng = a.get("lat"), a.get("lng")
                if not lat:
                    lat, lng = geocode(a["address"])
                    if lat:
                        ix = next((j for j,x in enumerate(st.session_state.addrs) if x["id"]==a["id"]), None)
                        if ix is not None:
                            st.session_state.addrs[ix]["lat"] = lat
                            st.session_state.addrs[ix]["lng"] = lng
                            updated = True
                if lat: dr.append({**a, "lat": lat, "lng": lng})
                else: st.warning(f"Skipping (could not geocode): {a['address']}")
            if updated: save("addrs", st.session_state.addrs)
            if not dr: st.error("No addresses could be geocoded."); st.stop()

        if "Today" in urgency:
            # ── Full route optimization ────────────────────────────────────────
            with st.spinner("Building distance matrix..."):
                pts  = [(v["lat"],v["lng"]) for v in vr] + [(d["lat"],d["lng"]) for d in dr]
                nv   = len(vr)
                fm   = osrm_matrix(tuple(tuple(p) for p in pts))

            with st.spinner("Optimizing routes..."):
                clusters = {i: [] for i in range(nv)}
                for di in range(len(dr)):
                    bv = min(range(nv), key=lambda vi: fm[vi][nv+di])
                    clusters[bv].append(nv+di)
                routes = []
                for vi, vol in enumerate(vr):
                    if not clusters[vi]: continue
                    order, dist = solve_tsp(fm, vi, clusters[vi])
                    stops = [dr[idx-nv] for idx in order]
                    wps   = [(vol["lat"],vol["lng"])]+[(s["lat"],s["lng"]) for s in stops]+[(vol["lat"],vol["lng"])]
                    routes.append({
                        "volunteer": vol, "stops": stops,
                        "distance_km": dist/0.621371, "distance_miles": dist,
                        "road_geometry": osrm_route(tuple(tuple(p) for p in wps)),
                        "color": COLORS[vi%len(COLORS)], "hex": HEX_COLORS[vi%len(HEX_COLORS)],
                    })
            st.session_state.routes = routes
            st.session_state.prox   = None
            rec = {"timestamp": datetime.now().strftime("%b %d, %Y at %I:%M %p"), "routes": routes}
            st.session_state.history = [rec] + (st.session_state.history or [])
            save("history", st.session_state.history)
            st.toast(f"Routes ready — {len(dr)} stops across {len(routes)} volunteers", icon="🗺️")

        else:
            # ── Proximity clustering ───────────────────────────────────────────
            clusters = {i: [] for i in range(len(vr))}
            for d in dr:
                best = min(range(len(vr)),
                    key=lambda vi: hav((vr[vi]["lat"],vr[vi]["lng"]),(d["lat"],d["lng"])))
                clusters[best].append(d)
            prox_routes = []
            for vi, vol in enumerate(vr):
                if not clusters[vi]: continue
                prox_routes.append({
                    "volunteer": vol, "stops": clusters[vi],
                    "distance_miles": "—", "road_geometry": None,
                    "color": COLORS[vi%len(COLORS)], "hex": HEX_COLORS[vi%len(HEX_COLORS)],
                })
            st.session_state.prox   = {
                "volunteers": vr, "clusters": clusters,
                "timestamp": datetime.now().strftime("%b %d, %Y at %I:%M %p")
            }
            st.session_state.routes = prox_routes
            st.toast(f"Proximity map ready — {len(dr)} stops grouped by nearest volunteer", icon="🗺️")

        st.success("Done! Head to the 🗺️ Map or 📧 Emails & Texts tab.")
