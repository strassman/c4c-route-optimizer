import streamlit as st
import folium
from streamlit_folium import st_folium
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.core import page_header, save, gmaps, COLORS, HEX_COLORS

st.set_page_config(page_title="Map", page_icon="🗺️", layout="wide")
page_header("🗺️ Map")

active_routes = st.session_state.get("routes", [])
prox          = st.session_state.get("prox", None)
show_map      = active_routes or prox or st.session_state.get("map_open")

if not show_map:
    st.info("Open the map to see all sign placements and pending addresses.")
    if st.button("🗺️ Open Map", type="primary"):
        st.session_state.map_open = True
        st.rerun()
    st.stop()

# Build map
center = [39.2904, -76.6122]
m      = folium.Map(location=center, zoom_start=11, tiles="CartoDB positron")
lats, lngs = [], []
done = st.session_state.done

if active_routes and not prox:
    # ── Active route map ───────────────────────────────────────────────────────
    for r in active_routes:
        vol   = r["volunteer"]
        hex_c = r["hex"]
        color = r["color"]
        if not vol.get("lat"): continue
        lats.append(vol["lat"]); lngs.append(vol["lng"])
        folium.Marker([vol["lat"],vol["lng"]],
            popup=folium.Popup(f"<b>Home: {vol['name']}</b><br>{vol['address']}", max_width=220),
            tooltip=f"Home: {vol['name']}",
            icon=folium.Icon(color=color, icon="home", prefix="fa")).add_to(m)
        if r.get("road_geometry"):
            folium.PolyLine(r["road_geometry"], color=hex_c, weight=4, opacity=0.8,
                            tooltip=f"{vol['name']}: {r.get('distance_miles','—')} mi").add_to(m)
        for i, stop in enumerate(r["stops"]):
            if not stop.get("lat"): continue
            lats.append(stop["lat"]); lngs.append(stop["lng"])
            prev    = vol["address"] if i==0 else r["stops"][i-1]["address"]
            key     = vol["name"]+"_"+str(i)
            is_done = key in done
            ct = f"<br>👤 {stop['contact']}" if stop.get("contact") else ""
            nt = f"<br>📝 {stop['note']}"    if stop.get("note")    else ""
            if is_done:
                folium.Marker([stop["lat"],stop["lng"]],
                    popup=folium.Popup(f"<b>✅ {stop['address']}</b>{ct}{nt}", max_width=230),
                    tooltip=f"Sign placed — {stop['address']}",
                    icon=folium.Icon(color="green", icon="check", prefix="fa")).add_to(m)
            else:
                folium.Marker([stop["lat"],stop["lng"]],
                    popup=folium.Popup(
                        f"<b>Stop {i+1} — {vol['name']}</b><br>{stop['address']}{ct}{nt}<br>"
                        f"<a href='{gmaps(prev,stop['address'])}' target='_blank'>Directions</a>",
                        max_width=250),
                    tooltip=f"Stop {i+1} — {vol['name']}",
                    icon=folium.DivIcon(
                        html=f'<div style="background:white;color:{hex_c};border:2px solid {hex_c};'
                             f'border-radius:50%;width:26px;height:26px;display:flex;align-items:center;'
                             f'justify-content:center;font-weight:bold;font-size:12px;">{i+1}</div>',
                        icon_size=(26,26), icon_anchor=(13,13))).add_to(m)

    lc1,lc2,lc3,_ = st.columns([1.2,1,1.5,4])
    lc1.markdown('<span style="color:#27ae60;font-size:16px;">&#9679;</span> Placed', unsafe_allow_html=True)
    lc2.markdown('<span style="color:#aaa;font-size:16px;">&#9679;</span> Pending', unsafe_allow_html=True)
    lc3.markdown('<span style="color:#555;font-size:13px;">📍 Active delivery run</span>', unsafe_allow_html=True)
    if st.button("🗺️ Switch to master map view"):
        st.session_state.routes = []
        st.rerun()

elif prox:
    # ── Proximity cluster map ──────────────────────────────────────────────────
    vols     = prox["volunteers"]
    clusters = prox["clusters"]
    for vi, vol in enumerate(vols):
        if not vol.get("lat"): continue
        hex_c = HEX_COLORS[vi%len(HEX_COLORS)]
        color = COLORS[vi%len(COLORS)]
        lats.append(vol["lat"]); lngs.append(vol["lng"])
        folium.Marker([vol["lat"],vol["lng"]],
            popup=folium.Popup(f"<b>{vol['name']}</b><br>{len(clusters[vi])} stops nearby", max_width=200),
            tooltip=f"{vol['name']} — {len(clusters[vi])} stops",
            icon=folium.Icon(color=color, icon="home", prefix="fa")).add_to(m)
        for stop in clusters[vi]:
            if not stop.get("lat"): continue
            lats.append(stop["lat"]); lngs.append(stop["lng"])
            folium.CircleMarker([stop["lat"],stop["lng"]],
                radius=10, color=hex_c, fill=True, fill_color=hex_c, fill_opacity=0.7,
                popup=folium.Popup(f"<b>{stop['address']}</b><br>→ {vol['name']}", max_width=220),
                tooltip=f"{stop['address']} → {vol['name']}").add_to(m)
            folium.PolyLine([[vol["lat"],vol["lng"]],[stop["lat"],stop["lng"]]],
                color=hex_c, weight=1.5, opacity=0.3, dash_array="6").add_to(m)

    st.markdown(f"**📅 Proximity clusters — {prox['timestamp']}**")
    leg = st.columns(min(len(vols),6))
    for vi, vol in enumerate(vols[:6]):
        leg[vi].markdown(
            f'<span style="color:{HEX_COLORS[vi%len(HEX_COLORS)]};font-size:18px;">&#9679;</span> '
            f'**{vol["name"]}** ({len(clusters[vi])})', unsafe_allow_html=True)
    if st.button("🗺️ Switch to master map view"):
        st.session_state.prox = None
        st.rerun()

else:
    # ── Master map ─────────────────────────────────────────────────────────────
    geocoded  = [a for a in st.session_state.addrs if a.get("lat") and a.get("lng")]
    ungeocoded = [a for a in st.session_state.addrs if not a.get("lat")]
    if ungeocoded:
        st.caption(f"⚠️ {len(ungeocoded)} address{'es' if len(ungeocoded)!=1 else ''} could not be located.")
    for a in geocoded:
        lats.append(a["lat"]); lngs.append(a["lng"])
        if a.get("status") == "delivered":
            folium.Marker([a["lat"],a["lng"]],
                popup=folium.Popup(f"<b>✅ {a['address']}</b><br>{a.get('delivered_date','')}", max_width=230),
                tooltip=f"Sign placed — {a['address']}",
                icon=folium.Icon(color="green", icon="check", prefix="fa")).add_to(m)
        else:
            folium.CircleMarker([a["lat"],a["lng"]],
                radius=7, color="#888", fill=True, fill_color="#bbb", fill_opacity=0.85,
                popup=folium.Popup(f"<b>⏳ {a['address']}</b>", max_width=220),
                tooltip=f"Pending — {a['address']}").add_to(m)
    lc1,lc2,_ = st.columns([1,1,6])
    lc1.markdown('<span style="color:#27ae60;font-size:16px;">&#9679;</span> Placed', unsafe_allow_html=True)
    lc2.markdown('<span style="color:#888;font-size:16px;">&#9679;</span> Pending', unsafe_allow_html=True)
    if not st.session_state.addrs:
        st.info("No addresses yet.")

if lats:
    m.location  = [sum(lats)/len(lats), sum(lngs)/len(lngs)]
    m.zoom_start = 12

st_folium(m, use_container_width=True, height=560)
