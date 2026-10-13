import os
from uuid import uuid4
import httpx
import streamlit as st

st.set_page_config(page_title="Expense assistant")
st.title("Expense assistant")
base = os.getenv("APP_API_URL")
if not base:
    st.info("Enable the AI Compose overlay to use chat. The warehouse dashboard works independently.")
    st.stop()
token = st.sidebar.text_input("Demo chat access token", type="password")
st.session_state.setdefault("chat_session", str(uuid4()))
st.session_state.setdefault("chat_messages", [])

def send(message, confirm=None):
    try:
        response = httpx.post(base.rstrip("/")+"/chat", headers={"X-Chat-Token": token},
            json={"session_id": st.session_state.chat_session, "message": message, "confirm_token": confirm}, timeout=70)
        if response.status_code != 200:
            st.error(f"Chat unavailable ({response.status_code}). Check the token and AI setup.")
            return
        result = response.json()
        st.session_state.chat_messages = (st.session_state.chat_messages+[result])[-6:]
        st.session_state.pending = result if result.get("confirm_token") else None
        st.rerun()
    except httpx.HTTPError:
        st.error("AI features offline. The analytics dashboard remains available.")

for result in st.session_state.chat_messages:
    with st.chat_message("user"):
        st.write(result.get("question", ""))
    with st.chat_message("assistant"):
        if result.get("test_mode"):
            st.caption("TEST DOUBLE — not a real LLM response")
        st.write(result["response"])
        with st.expander("Show sources"):
            st.json(result.get("sources", [])); st.write(result.get("citations", []))
        if result.get("sql"):
            with st.expander("Show SQL and warehouse rows"):
                st.code(result["sql"], language="sql"); st.json(result.get("rows", []))
                st.caption("Latest warehouse load: "+result.get("latest_load", "unknown"))
pending = st.session_state.get("pending")
if pending:
    st.json(pending["draft"])
    if st.button("Confirm exact change", disabled=not token):
        send("Confirm", pending["confirm_token"])
if message := st.chat_input("Ask about the pipeline, spending, or an expense change", disabled=not token):
    with st.chat_message("user"):
        st.write(message)
    send(message)
