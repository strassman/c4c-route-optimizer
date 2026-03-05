import streamlit as st
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.core import page_header, save, gmaps
from datetime import datetime

st.set_page_config(page_title="Routes", page_icon="📍", layout="wide")
page_header("📍 Route History")

history = st.session_state.history
if not history:
    st.info("No routes yet. Run the optimizer on the 🚐 Delivery Run page.")
    st.stop()

done = st.session_state.done

for ri, rec in enumerate(history):
    rh1, rh2 = st.columns([7, 2])
    with rh1:
        st.markdown(f"### 📅 {rec.get('timestamp','Unknown')}")
        st.caption("🟢 Most recent" if ri==0 else f"Run #{len(history)-ri}")
    with rh2:
        ck = f"confirm_del_{ri}"
        if st.session_state.get(ck):
            cy, cn2 = st.columns(2)
            with cy:
                if st.button("✅ Yes", key=f"yes_{ri}", type="primary"):
                    history.pop(ri)
                    save("history", history)
                    st.session_state.routes = history[0]["routes"] if history else []
                    st.session_state[ck] = False
                    st.rerun()
            with cn2:
                if st.button("Cancel", key=f"no_{ri}"):
                    st.session_state[ck] = False
                    st.rerun()
        else:
            if st.button("🗑️ Delete Run", key=f"del_{ri}", use_container_width=True):
                st.session_state[ck] = True
                st.rerun()

    for r in rec["routes"]:
        v = r["volunteer"]
        with st.expander(f"**{v['name']}** — {len(r['stops'])} stops · {r.get('distance_miles','—')} mi",
                         expanded=(ri==0)):
            for i, s in enumerate(r["stops"]):
                prev    = v["address"] if i==0 else r["stops"][i-1]["address"]
                k       = v["name"]+"_"+str(i)
                is_done = k in done
                kc, ki  = st.columns([1, 11])
                with kc:
                    chk = st.checkbox("", value=is_done, key=f"c_{ri}_{k}")
                    if chk and not is_done:
                        done[k] = {"key":k,"address":s["address"],"lat":s.get("lat"),"lng":s.get("lng"),
                            "volunteer":v["name"],"stop_num":i+1,
                            "delivered_date":datetime.now().strftime("%b %d, %Y")}
                        st.session_state.done = done
                        save("done", list(done.values()))
                        ix = next((j for j,a in enumerate(st.session_state.addrs) if a.get("id")==s.get("id")), None)
                        if ix is not None:
                            st.session_state.addrs[ix]["status"] = "delivered"
                            st.session_state.addrs[ix]["delivered_date"] = datetime.now().strftime("%b %d, %Y")
                            save("addrs", st.session_state.addrs)
                        st.rerun()
                    elif not chk and is_done:
                        del st.session_state.done[k]
                        save("done", list(st.session_state.done.values()))
                        ix = next((j for j,a in enumerate(st.session_state.addrs) if a.get("id")==s.get("id")), None)
                        if ix is not None:
                            st.session_state.addrs[ix]["status"] = "pending"
                            save("addrs", st.session_state.addrs)
                        st.rerun()
                with ki:
                    ct = f" · 👤 {s['contact']}" if s.get("contact") else ""
                    nt = f" — *{s['note']}*"     if s.get("note")    else ""
                    if is_done:
                        dd = done.get(k,{}).get("delivered_date","")
                        st.markdown(f"~~**Stop {i+1}:** {s['address']}~~ ✅{ct}{nt}"+(f" · {dd}" if dd else ""))
                    else:
                        st.markdown(f"**Stop {i+1}:** {s['address']}{ct}{nt}")
                    st.markdown(f"[Directions]({gmaps(prev,s['address'])})")
    st.divider()
