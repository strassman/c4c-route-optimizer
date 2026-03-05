import streamlit as st
import urllib.parse
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.core import page_header, mailto, gen_email

st.set_page_config(page_title="Emails & Texts", page_icon="📧", layout="wide")
page_header("📧 Emails & Texts")

routes = st.session_state.get("routes", [])
if not routes:
    st.info("Run the optimizer on the 🚐 Delivery Run page first.")
    st.stop()

cname = st.session_state.get("cname", "the Campaign")
subj  = f"{cname} - Your Yard Sign Delivery Route"

all_em = [r["volunteer"].get("email","") for r in routes if r["volunteer"].get("email")]
all_ph = [r["volunteer"].get("phone","") for r in routes if r["volunteer"].get("phone")]

# ── Bulk buttons ───────────────────────────────────────────────────────────────
ec, tc = st.columns(2)
with ec:
    if all_em:
        bodies = "\n\n---\n\n".join([gen_email(r) for r in routes])
        st.markdown(
            f'<a href="{mailto(",".join(all_em), subj, bodies)}" '
            f'style="display:inline-block;padding:12px 24px;background:#2563eb;color:white;'
            f'border-radius:8px;text-decoration:none;font-weight:600;font-size:15px;">'
            f'📧 Email All Volunteers</a>', unsafe_allow_html=True)
        st.caption(f"{len(all_em)} recipients")
    else:
        st.warning("No volunteer emails on file.")

with tc:
    if all_ph:
        nums  = ",".join([r["volunteer"]["phone"].translate(str.maketrans("","","- ()"))
                          for r in routes if r["volunteer"].get("phone")])
        lines = "\n".join([
            f"{r['volunteer']['name']}: " +
            ", ".join([s["address"].split(",")[0] for s in r["stops"][:3]]) +
            ("…" if len(r["stops"])>3 else "")
            for r in routes])
        txt = f"{cname} delivery run:\n{lines}\nFull details by email. Thank you!"
        st.markdown(
            f'<a href="sms:{nums}&body={urllib.parse.quote(txt)}" '
            f'style="display:inline-block;padding:12px 24px;background:#16a34a;color:white;'
            f'border-radius:8px;text-decoration:none;font-weight:600;font-size:15px;">'
            f'💬 Text All Volunteers</a>', unsafe_allow_html=True)
        st.caption(f"{len(all_ph)} recipients")
    else:
        st.warning("No volunteer phones on file.")

st.divider()

# ── Per volunteer ──────────────────────────────────────────────────────────────
for r in routes:
    v    = r["volunteer"]
    ve   = v.get("email","")
    vp   = v.get("phone","").translate(str.maketrans("","","- ()"))
    body = gen_email(r)

    stops_txt = "\n".join([f"  {i+1}. {s['address'].split(',')[0]}" for i,s in enumerate(r["stops"])])
    txt = (f"Hi {v['name']}! {cname} here. "
           f"{len(r['stops'])} stop{'s' if len(r['stops'])!=1 else ''} today:\n"
           f"{stops_txt}\nFull route by email!")

    with st.expander(f"{v['name']} — {ve or 'no email'} · {v.get('phone','no phone')}", expanded=True):
        bc1, bc2 = st.columns(2)
        with bc1:
            st.markdown("**📧 Email**")
            if ve:
                st.markdown(
                    f'<a href="{mailto(ve,subj,body)}" '
                    f'style="display:inline-block;padding:10px 20px;background:#2563eb;color:white;'
                    f'border-radius:8px;text-decoration:none;font-weight:600;">Open in Mail</a>',
                    unsafe_allow_html=True)
                st.caption(f"To: {ve}")
            else:
                st.warning("No email on file.")
        with bc2:
            st.markdown("**💬 Text**")
            if vp:
                st.markdown(
                    f'<a href="sms:{vp}&body={urllib.parse.quote(txt)}" '
                    f'style="display:inline-block;padding:10px 20px;background:#16a34a;color:white;'
                    f'border-radius:8px;text-decoration:none;font-weight:600;">Open in Messages</a>',
                    unsafe_allow_html=True)
                st.caption(f"To: {v.get('phone','')}")
            else:
                st.warning("No phone on file.")
        st.text_area("Email body:", value=body, height=200, key=f"eb_{v['name']}")
