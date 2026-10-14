"""Streamlit front end for Sahayak. Runs the LangGraph agent in-process.

Run locally:  streamlit run streamlit_app.py
"""
import sqlite3
import time
import uuid

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app import config, db, rag
from app.graph import build_graph

st.set_page_config(page_title="Sahayak - Academic Helpdesk", page_icon="🎓", layout="wide")

SUGGESTIONS = [
    "How many lectures can I still miss in Cloud Computing?",
    "What is the minimum attendance needed for the end-sem exam?",
    "Show my CA marks",
    "Request re-evaluation of my ML CA2 marks, question 3 was not checked",
]

DRAFT_LABELS = {
    "request_type": "Request", "leave_type": "Leave type", "start_date": "From",
    "end_date": "To", "days": "Days", "course": "Course", "component": "Component",
    "current_marks": "Current marks", "purpose": "Purpose", "reason": "Reason",
    "approver_name": "Will be sent to",
}


@st.cache_resource(show_spinner="Loading regulations and building the vector index...")
def load_agent():
    db.init_db()
    rag.build_index()
    conn = sqlite3.connect(config.CHECKPOINT_DB, check_same_thread=False)
    return build_graph(SqliteSaver(conn))


def run_agent(graph, payload) -> dict:
    cfg = {"configurable": {"thread_id": st.session_state.thread_id}, "recursion_limit": 20}
    before = graph.get_state(cfg)
    n_trace = len(before.values.get("trace", [])) if before.values else 0
    n_msgs = len(before.values.get("messages", [])) if before.values else 0
    started = time.perf_counter()
    graph.invoke(payload, cfg)
    after = graph.get_state(cfg)
    new = after.values["messages"][n_msgs:]
    replies = [m.content for m in new if isinstance(m, AIMessage) and not m.tool_calls]
    pending = None
    for task in after.tasks:
        for intr in task.interrupts:
            pending = intr.value["draft"]
    return {"reply": replies[-1] if replies else "", "trace": after.values["trace"][n_trace:],
            "pending": pending, "intent": after.values.get("intent"),
            "seconds": round(time.perf_counter() - started, 2)}


def record(result):
    if result["reply"]:
        st.session_state.chat.append(
            {"role": "assistant", "content": result["reply"],
             "meta": f"{result['intent']} · {result['seconds']} s"})
    st.session_state.pending = result["pending"]
    st.session_state.trace = result["trace"]


def ask(graph, text):
    st.session_state.chat.append({"role": "user", "content": text})
    with st.spinner("Sahayak is thinking..."):
        try:
            record(run_agent(graph, {"messages": [HumanMessage(text[:1500])],
                                     "prn": st.session_state.student["prn"]}))
        except Exception as e:  # show the error instead of a blank screen
            st.session_state.chat.append({"role": "assistant", "content": f"Error: {e}"})


def decide(graph, decision):
    with st.spinner("Sending..." if decision == "approve" else "Cancelling..."):
        record(run_agent(graph, Command(resume=decision)))


graph = load_agent()

# ---------- sign in ----------
if "student" not in st.session_state:
    st.title("🎓 Sahayak")
    st.caption("Agentic academic helpdesk for Division C · prototype with sample data")
    students = db.list_students()
    choice = st.selectbox("Sign in with your PRN (demo accounts)",
                          [f"{s['prn']} - {s['name']}" for s in students])
    if st.button("Start session", type="primary"):
        st.session_state.student = db.get_student(choice.split(" - ")[0])
        st.session_state.thread_id = f"{st.session_state.student['prn']}:{uuid.uuid4().hex[:12]}"
        first = st.session_state.student["name"].split()[0]
        st.session_state.chat = [{"role": "assistant", "content":
                                  f"Hi {first}! I'm Sahayak. Ask me about rules, your attendance, "
                                  "marks or exam dates, or ask me to prepare a leave, CA "
                                  "re-evaluation or bonafide certificate request."}]
        st.session_state.pending = None
        st.session_state.trace = []
        st.rerun()
    st.stop()

student = st.session_state.student

# ---------- sidebar: trace and requests ----------
with st.sidebar:
    st.markdown(f"**{student['name']}**  \nPRN {student['prn']} · Division {student['division']}")
    if st.button("Sign out"):
        st.session_state.clear()
        st.rerun()
    st.divider()
    st.subheader("Agent trace")
    st.caption("Nodes and tools used for the last message")
    if st.session_state.trace:
        for i, t in enumerate(st.session_state.trace, start=1):
            st.markdown(f"{i}. **{t['node']}** - {t['detail']}")
    else:
        st.caption("No messages yet")
    st.divider()
    st.subheader("My requests")
    reqs = db.get_requests(student["prn"])
    if not reqs:
        st.caption("None yet")
    for r in reqs:
        st.markdown(f"**{r['ref']}** · {r['type']}  \n:gray[{r['status']}, {r['dispatch_status']}]")

# ---------- chat ----------
st.title("🎓 Sahayak")
for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("meta"):
            st.caption(m["meta"])

if st.session_state.pending:
    d = st.session_state.pending
    with st.chat_message("assistant"):
        st.warning("Please review this request before I send it to the faculty.")
        st.table({DRAFT_LABELS[k]: [str(d[k])] for k in DRAFT_LABELS if d.get(k)})
        for note in d.get("notes", []):
            st.caption(f"• {note}")
        c1, c2, _ = st.columns([1, 1, 4])
        if c1.button("Approve and send", type="primary"):
            decide(graph, "approve")
            st.rerun()
        if c2.button("Cancel"):
            decide(graph, "cancel")
            st.rerun()
else:
    cols = st.columns(len(SUGGESTIONS))
    for col, s in zip(cols, SUGGESTIONS):
        if col.button(s, use_container_width=True):
            ask(graph, s)
            st.rerun()
    if text := st.chat_input("Ask about attendance, marks, rules, or apply for leave..."):
        ask(graph, text)
        st.rerun()
