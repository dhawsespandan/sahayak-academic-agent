"""The Sahayak agent, built as a LangGraph state graph.

    START -> guard -> router -> policy_rag ---------------------------> END
                             -> personal_agent <-> tools -------------> END
                             -> extract_request -> check_rules -> human_approval -> dispatch -> END
                             -> general_reply ------------------------> END

Each node appends a short entry to state["trace"] so the UI and the tests can
see which path the agent actually took.
"""
import operator
import re
from datetime import date
from typing import Annotated, Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from app import config, db, n8n_client, prompts, rag, rules
from app.tools import PERSONAL_TOOLS


class State(TypedDict):
    messages: Annotated[list, add_messages]
    prn: str
    intent: Optional[str]
    draft: Optional[dict]
    trace: Annotated[list, operator.add]


# ---------- models ----------

def _llm(model: str, temperature: float = 0.0) -> ChatGroq:
    return ChatGroq(model=model, temperature=temperature, api_key=config.GROQ_API_KEY,
                    max_retries=2)


class Route(BaseModel):
    intent: Literal["policy", "personal", "action", "general", "out_of_scope"]


class RequestDraft(BaseModel):
    request_type: Optional[Literal["leave", "reevaluation", "bonafide"]] = None
    leave_type: Optional[Literal["personal", "medical", "duty"]] = None
    start_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    course: Optional[str] = Field(None, description="course code, e.g. ML")
    component: Optional[Literal["CA1", "CA2"]] = None
    purpose: Optional[str] = None
    reason: Optional[str] = None


REQUIRED_FIELDS = {
    "leave": ["leave_type", "start_date", "end_date", "reason"],
    "reevaluation": ["course", "component", "reason"],
    "bonafide": ["purpose"],
}


def _recent(messages: list) -> list:
    """Last few conversation turns, without tool-call plumbing, for the LLM."""
    convo = [m for m in messages if isinstance(m, HumanMessage)
             or (isinstance(m, AIMessage) and not m.tool_calls)]
    return convo[-config.HISTORY_WINDOW:]


# gpt-oss often writes narrow no-break spaces and non-breaking hyphens ("5\u202fworking days"),
# which break plain-text search and copy-paste. Replace them with normal characters.
_UNICODE_FIXES = str.maketrans({"\u202f": " ", "\u00a0": " ", "\u2011": "-", "\u2013": "-"})


def _clean(text: str) -> str:
    return text.translate(_UNICODE_FIXES)


def _latest_user_text(state: State) -> str:
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            return m.content
    return ""


# ---------- nodes ----------

PRN_PATTERN = re.compile(r"\b\d{11}\b")
INJECTION_PATTERN = re.compile(
    r"ignore (all |the )?(previous|above|prior) (instructions|rules)|system prompt|"
    r"you are now|act as (an? )?admin|developer mode", re.I)


def guard(state: State) -> dict:
    """Deterministic input checks before any LLM sees the message."""
    text = _latest_user_text(state)
    other = [p for p in PRN_PATTERN.findall(text) if p != state["prn"]]
    if other:
        reply = ("For privacy, I can only show your own records. I cannot look up another "
                 "student's data.")
        return {"messages": [AIMessage(reply)], "intent": "blocked",
                "trace": [{"node": "guard", "detail": f"blocked: other PRN {other[0]}"}]}
    if INJECTION_PATTERN.search(text):
        reply = "I can't change how I work. Please ask an academic question."
        return {"messages": [AIMessage(reply)], "intent": "blocked",
                "trace": [{"node": "guard", "detail": "blocked: prompt-injection pattern"}]}
    return {"intent": None, "trace": [{"node": "guard", "detail": "passed"}]}


def router(state: State) -> dict:
    llm = _llm(config.ROUTER_MODEL).with_structured_output(Route)
    route = llm.invoke([SystemMessage(prompts.ROUTER_PROMPT), *_recent(state["messages"])])
    # A request that is still waiting for missing details keeps the conversation in the action flow
    # only if the router agrees; otherwise the student has moved on and the draft is dropped.
    update = {"intent": route.intent, "trace": [{"node": "router", "detail": route.intent}]}
    if route.intent != "action":
        update["draft"] = None
    return update


def policy_rag(state: State) -> dict:
    question = _latest_user_text(state)
    hits = rag.search(question)
    good = [h for h in hits if h["score"] >= config.RAG_MIN_SCORE]
    detail = ", ".join(f"{h['source']} p{h['page']} ({h['score']})" for h in hits)
    if not good:
        reply = ("I could not find this in the institute regulations I have. Please check with your "
                 "faculty mentor.")
        return {"messages": [AIMessage(reply)],
                "trace": [{"node": "policy_rag", "detail": f"no chunk above threshold: {detail}"}]}
    system = prompts.POLICY_PROMPT.format(context=rag.format_context(good))
    answer = _llm(config.LLM_MODEL).invoke([SystemMessage(system), *_recent(state["messages"])])
    return {"messages": [AIMessage(_clean(answer.content))],
            "trace": [{"node": "policy_rag", "detail": f"retrieved: {detail}"}]}


def personal_agent(state: State) -> dict:
    student = db.get_student(state["prn"])
    today = config.today()
    system = prompts.PERSONAL_PROMPT.format(name=student["name"], prn=student["prn"],
                                            division=student["division"], today=today.isoformat())
    # keep the current turn's tool calls/results so the model can see them
    turn_start = max(i for i, m in enumerate(state["messages"]) if isinstance(m, HumanMessage))
    history = _recent(state["messages"][:turn_start])
    current = state["messages"][turn_start:]
    llm = _llm(config.LLM_MODEL).bind_tools(PERSONAL_TOOLS)
    response = llm.invoke([SystemMessage(system), *history, *current])
    if not response.tool_calls:
        response.content = _clean(response.content)
    if response.tool_calls:
        calls = ", ".join(f"{c['name']}({c['args']})" for c in response.tool_calls)
        entry = {"node": "personal_agent", "detail": f"calls {calls}"}
    else:
        entry = {"node": "personal_agent", "detail": "final answer"}
    return {"messages": [response], "trace": [entry]}


_tool_node = ToolNode(PERSONAL_TOOLS)


def tools(state: State) -> dict:
    result = _tool_node.invoke(state)
    names = [m.name for m in result["messages"] if isinstance(m, ToolMessage)]
    return {"messages": result["messages"],
            "trace": [{"node": "tools", "detail": f"ran {', '.join(names)}"}]}


def extract_request(state: State) -> dict:
    today = config.today()
    courses = ", ".join(f"{c['code']} ({c['name']})" for c in db.get_courses())
    system = prompts.EXTRACT_PROMPT.format(today=today.isoformat(), weekday=today.strftime("%A"),
                                           courses=courses)
    llm = _llm(config.LLM_MODEL).with_structured_output(RequestDraft)
    draft = llm.invoke([SystemMessage(system), *_recent(state["messages"])]).model_dump()
    # merge with what was collected in earlier turns
    previous = state.get("draft") or {}
    merged = {k: draft.get(k) or previous.get(k) for k in RequestDraft.model_fields}
    rtype = merged["request_type"]
    missing = REQUIRED_FIELDS.get(rtype, ["request_type"]) if rtype else ["request_type"]
    missing = [f for f in missing if not merged.get(f)]
    merged["missing"] = missing
    return {"draft": merged,
            "trace": [{"node": "extract_request",
                       "detail": f"type={rtype}, missing={missing or 'none'}"}]}


FIELD_QUESTIONS = {
    "request_type": "what you want to submit (leave, CA re-evaluation or a bonafide certificate)",
    "leave_type": "the type of leave (personal, medical or duty)",
    "start_date": "the first day of leave",
    "end_date": "the last day of leave",
    "reason": "the reason",
    "course": "the course",
    "component": "which CA component (CA1 or CA2)",
    "purpose": "the purpose of the certificate",
}


def check_rules(state: State) -> dict:
    """Deterministic policy checks; decides the approver and blocks invalid requests."""
    d = dict(state["draft"])
    if d["missing"]:
        ask = "; ".join(FIELD_QUESTIONS[f] for f in d["missing"])
        return {"messages": [AIMessage(f"Sure, I can prepare that. Please tell me {ask}.")],
                "trace": [{"node": "check_rules", "detail": "asked for missing details"}]}

    student = db.get_student(state["prn"])
    today = config.today()
    problems, notes = [], []
    approver = db.get_faculty(student["mentor_id"])

    if d["request_type"] == "leave":
        try:
            start, end = date.fromisoformat(d["start_date"]), date.fromisoformat(d["end_date"])
        except ValueError:
            problems.append("I could not understand the dates; please give them as DD-MM-YYYY.")
        else:
            check = rules.leave_rules(d["leave_type"], start, end, today)
            problems += check["problems"]
            notes += check["notes"]
            d["days"] = check["days"]
            if check["approver"] == "hod":
                approver = db.get_hod()
    elif d["request_type"] == "reevaluation":
        marks = [m for m in db.get_ca_marks(state["prn"])
                 if m["course_code"] == (d["course"] or "").upper() and m["component"] == d["component"]]
        if not marks:
            problems.append(f"No published {d['component']} marks found for course {d['course']}.")
        else:
            m = marks[0]
            window = rules.reevaluation_window(date.fromisoformat(m["published_on"]), today)
            d["current_marks"] = f"{m['marks']:g}/{m['max_marks']:g}"
            if not window["open"]:
                problems.append(f"The re-evaluation window for {d['course']} {d['component']} closed "
                                f"on {window['deadline']} (5 working days after the marks were "
                                f"published on {window['published_on']}).")
            else:
                notes.append(f"Window open until {window['deadline']}. Marks may go up or down.")
            course_row = next(c for c in db.get_courses() if c["code"] == m["course_code"])
            approver = db.get_faculty(course_row["faculty_id"])

    if problems:
        reply = "I can't submit this request:\n- " + "\n- ".join(problems)
        return {"messages": [AIMessage(reply)], "draft": None,
                "trace": [{"node": "check_rules", "detail": f"rejected: {problems}"}]}

    d.update({"student_name": student["name"], "prn": student["prn"], "notes": notes,
              "approver_id": approver["id"], "approver_name": approver["name"],
              "approver_email": approver["email"]})
    return {"draft": d,
            "trace": [{"node": "check_rules", "detail": f"ok, approver={approver['name']}"}]}


def human_approval(state: State) -> dict:
    """Pauses the graph. The UI shows the draft and resumes with 'approve' or 'cancel'."""
    decision = interrupt({"type": "approval_needed", "draft": state["draft"]})
    entry = {"node": "human_approval", "detail": f"student chose: {decision}"}
    if decision != "approve":
        return {"messages": [AIMessage("Okay, I have cancelled this request. Nothing was sent.")],
                "draft": None, "intent": "cancelled", "trace": [entry]}
    return {"intent": "approved", "trace": [entry]}


REQUEST_NAMES = {"leave": "leave", "reevaluation": "CA re-evaluation",
                 "bonafide": "bonafide certificate"}


def dispatch(state: State) -> dict:
    d = state["draft"]
    name = REQUEST_NAMES[d["request_type"]]
    ref = db.next_request_ref()
    payload = {
        "ref": ref,
        "request_type": d["request_type"],
        "student_name": d["student_name"],
        "prn": d["prn"],
        "to": config.FACULTY_EMAIL_OVERRIDE or d["approver_email"],
        "approver_name": d["approver_name"],
        "subject": f"[Sahayak {ref}] {name.capitalize()} request from {d['student_name']}",
        "details": {k: v for k, v in d.items()
                    if k in ("leave_type", "start_date", "end_date", "days", "course", "component",
                             "current_marks", "purpose", "reason") and v},
        "notes": d.get("notes", []),
    }
    result = n8n_client.dispatch(payload)
    status = "sent_to_faculty" if result["ok"] else "stored_pending_dispatch"
    db.save_request(ref, d["prn"], d["request_type"], payload["details"], d["approver_id"],
                    "submitted", status)
    if result["ok"]:
        reply = (f"Done. Your {name} request has been sent to {d['approver_name']}. "
                 f"Reference number: {ref}.")
    else:
        reply = (f"Your {name} request is saved with reference {ref}, but I could not "
                 f"reach the notification service, so it will be forwarded by the office.")
    if d.get("notes"):
        reply += "\nNote: " + " ".join(d["notes"])
    return {"messages": [AIMessage(reply)], "draft": None,
            "trace": [{"node": "dispatch", "detail": f"{ref}: {status} ({result['detail'][:80]})"}]}


def general_reply(state: State) -> dict:
    if state["intent"] == "out_of_scope":
        return {"messages": [AIMessage(prompts.OUT_OF_SCOPE_REPLY)],
                "trace": [{"node": "general_reply", "detail": "out of scope"}]}
    answer = _llm(config.ROUTER_MODEL, 0.3).invoke(
        [SystemMessage(prompts.GENERAL_PROMPT), *_recent(state["messages"])])
    return {"messages": [AIMessage(_clean(answer.content))],
            "trace": [{"node": "general_reply", "detail": "greeting/help"}]}


# ---------- edges ----------

def after_guard(state: State) -> str:
    return END if state["intent"] == "blocked" else "router"


def after_router(state: State) -> str:
    return {"policy": "policy_rag", "personal": "personal_agent", "action": "extract_request"
            }.get(state["intent"], "general_reply")


def after_personal(state: State) -> str:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


def after_check(state: State) -> str:
    d = state.get("draft")
    return "human_approval" if d and not d.get("missing") and d.get("approver_id") else END


def after_approval(state: State) -> str:
    return "dispatch" if state["intent"] == "approved" else END


def build_graph(checkpointer=None):
    g = StateGraph(State)
    for name, fn in [("guard", guard), ("router", router), ("policy_rag", policy_rag),
                     ("personal_agent", personal_agent), ("tools", tools),
                     ("extract_request", extract_request), ("check_rules", check_rules),
                     ("human_approval", human_approval), ("dispatch", dispatch),
                     ("general_reply", general_reply)]:
        g.add_node(name, fn)
    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", after_guard, ["router", END])
    g.add_conditional_edges("router", after_router,
                            ["policy_rag", "personal_agent", "extract_request", "general_reply"])
    g.add_conditional_edges("personal_agent", after_personal, ["tools", END])
    g.add_edge("tools", "personal_agent")
    g.add_edge("extract_request", "check_rules")
    g.add_conditional_edges("check_rules", after_check, ["human_approval", END])
    g.add_conditional_edges("human_approval", after_approval, ["dispatch", END])
    g.add_edge("dispatch", END)
    g.add_edge("policy_rag", END)
    g.add_edge("general_reply", END)
    return g.compile(checkpointer=checkpointer)
