import streamlit as st
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from utils.core import db, hp, signup

st.set_page_config(page_title="Campaign Route Optimizer", page_icon="🗺️", layout="wide")

# If already logged in, show a welcome and navigation
if "cid" in st.session_state:
    cname = st.session_state.get("cname","Campaign")
    st.title(f"🗺️ {cname} — Yard Sign Route Optimizer")
    st.success(f"Logged in as **{cname}**")
    st.markdown("Use the sidebar to navigate between pages.")
    if st.button("Log Out"):
        st.session_state.clear()
        st.rerun()
    st.stop()

# Auth page
st.title("🗺️ Campaign Yard Sign Route Optimizer")
st.caption("Manage volunteers, plan deliveries, and track every sign on the map.")
st.divider()

lt, st2 = st.tabs(["🔑 Log In", "✨ Sign Up"])

with lt:
    st.subheader("Log in to your campaign")
    em = st.text_input("Email", key="li_em", placeholder="you@campaign.com")
    pw = st.text_input("Password", key="li_pw", type="password")
    if st.button("Log In", type="primary", use_container_width=True):
        if em and pw:
            try:
                res = db().table("campaign_accounts").select("*").eq("email", em.lower().strip()).execute()
                if not res.data:
                    st.error("No account found with that email.")
                elif res.data[0]["password_hash"] != hp(pw):
                    st.error("Wrong password.")
                else:
                    a = res.data[0]
                    st.session_state.cid   = a["id"]
                    st.session_state.cname = a["campaign_name"]
                    st.rerun()
            except Exception as e:
                st.error(str(e))
        else:
            st.warning("Enter email and password.")

with st2:
    st.subheader("Create a new campaign account")
    cn  = st.text_input("Campaign name", key="su_cn", placeholder="Smith for State Senate")
    em2 = st.text_input("Email", key="su_em", placeholder="you@campaign.com")
    pw2 = st.text_input("Password", key="su_pw", type="password")
    pw3 = st.text_input("Confirm password", key="su_pw2", type="password")
    if st.button("Create Account", type="primary", use_container_width=True):
        if not cn or not em2 or not pw2:
            st.warning("All fields required.")
        elif pw2 != pw3:
            st.error("Passwords don't match.")
        elif len(pw2) < 6:
            st.error("Password must be 6+ characters.")
        else:
            nid, err = signup(cn, em2, pw2)
            if err:
                st.error(err)
            else:
                st.session_state.cid   = nid
                st.session_state.cname = cn
                st.rerun()
