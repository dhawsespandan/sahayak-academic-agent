"""Runs every case in eval/cases.py through the real agent and writes
eval/results.json and eval/results.md.

Usage: python -m eval.run_eval            (needs GROQ_API_KEY)
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("APP_TODAY", "2026-10-14")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="sahayak-eval-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app import config, db, rag  # noqa: E402
from app.graph import build_graph  # noqa: E402
from eval.cases import CASES, ISHA  # noqa: E402

OUT = Path(__file__).resolve().parent


def run_case(graph, case):
    cfg = {"configurable": {"thread_id": f"eval-{case['id']}"}, "recursion_limit": 20}
    started = time.perf_counter()
    for turn in case["turns"]:
        before = graph.get_state(cfg)
        n_msgs = len(before.values.get("messages", [])) if before.values else 0
        n_trace = len(before.values.get("trace", [])) if before.values else 0
        if turn in ("approve", "cancel"):
            graph.invoke(Command(resume=turn), cfg)
        else:
            graph.invoke({"messages": [HumanMessage(turn)], "prn": case["prn"]}, cfg)
    seconds = round(time.perf_counter() - started, 2)
    state = graph.get_state(cfg)
    new = state.values["messages"][n_msgs:]
    reply = next((m.content for m in reversed(new)
                  if isinstance(m, AIMessage) and not m.tool_calls), "")
    trace = state.values["trace"][n_trace:]
    all_trace = state.values["trace"]
    tools_used = [m.name for m in state.values["messages"] if isinstance(m, ToolMessage)]
    pending = None
    for task in state.tasks:
        for intr in task.interrupts:
            pending = intr.value["draft"]
    intents = [t["detail"] for t in all_trace if t["node"] == "router"]
    if any(t["node"] == "guard" and t["detail"].startswith("blocked") for t in all_trace):
        intents.append("blocked")
    return {"reply": reply, "trace": trace, "tools_used": tools_used, "pending": pending,
            "intent": intents[0] if intents else None, "seconds": seconds,
            "nodes": [t["node"] for t in all_trace]}


def check(case, r):
    failures = []
    allowed = case["intent"] if isinstance(case["intent"], list) else [case["intent"]]
    if r["intent"] not in allowed:
        failures.append(f"intent {r['intent']} != {case['intent']}")
    text = r["reply"].lower()
    if "expect_any" in case and not any(s.lower() in text for s in case["expect_any"]):
        failures.append(f"reply lacks any of {case['expect_any']}")
    if "expect_node" in case and case["expect_node"] not in r["nodes"]:
        failures.append(f"node {case['expect_node']} not visited")
    if "expect_tool" in case and case["expect_tool"] not in r["tools_used"]:
        failures.append(f"tool {case['expect_tool']} not called")
    if "expect_tool_arg" in case:
        calls = " ".join(t["detail"] for t in r["trace"] if t["node"] == "personal_agent")
        if not any(a in calls for a in case["expect_tool_arg"]):
            failures.append(f"tool not called with any of {case['expect_tool_arg']}")
    if "expect_marks" in case:
        code, comp = case["expect_marks"]
        m = next(x for x in db.get_ca_marks(case["prn"])
                 if x["course_code"] == code and x["component"] == comp)
        value = f"{m['marks']:g}"
        if value not in r["reply"]:
            failures.append(f"exact mark {value} missing")
    if "expect_pending" in case:
        if not r["pending"]:
            failures.append("no approval interrupt")
        else:
            for k, v in case["expect_pending"].items():
                if r["pending"].get(k) != v:
                    failures.append(f"draft {k}={r['pending'].get(k)!r} != {v!r}")
    if case.get("expect_no_pending") and r["pending"]:
        failures.append("unexpected approval interrupt")
    if case.get("expect_no_dispatch") and "dispatch" in r["nodes"]:
        failures.append("dispatch ran after cancel")
    if case.get("expect_saved") and not db.get_requests(case["prn"]):
        failures.append("request not saved in DB")
    if case.get("expect_not_isha"):
        isha_ml = [f"{x['marks']:g}" for x in db.get_ca_marks(ISHA) if x["course_code"] == "ML"]
        own_ml = [f"{x['marks']:g}" for x in db.get_ca_marks(case["prn"]) if x["course_code"] == "ML"]
        leaked = [v for v in isha_ml if v not in own_ml and v in r["reply"]]
        if leaked:
            failures.append(f"possible leak of {leaked}")
    return failures


def main():
    db.init_db(reset=True)
    rag.build_index(force=True)
    graph = build_graph(InMemorySaver())
    results = []
    for case in CASES:
        try:
            r = run_case(graph, case)
            fails = check(case, r)
        except Exception as e:  # keep going, record the error as a failure
            r, fails = {"reply": "", "trace": [], "intent": None, "seconds": 0}, [f"error: {e}"]
        status = "PASS" if not fails else "FAIL"
        print(f"{case['id']:3} {status}  intent={r['intent']}  {r['seconds']}s  {fails or ''}")
        results.append({"id": case["id"], "turns": case["turns"], "expected_intent": case["intent"],
                        "status": status, "failures": fails, "note": case.get("note", ""), **r})
        time.sleep(2)  # stay under the Groq free-tier rate limit

    passed = sum(r["status"] == "PASS" for r in results)
    routed = sum(r["intent"] in (r["expected_intent"] if isinstance(r["expected_intent"], list)
                                 else [r["expected_intent"]]) for r in results)
    latencies = [r["seconds"] for r in results if r["seconds"]]
    summary = {"model": config.LLM_MODEL, "router_model": config.ROUTER_MODEL,
               "cases": len(results), "passed": passed, "routing_correct": routed,
               "avg_seconds": round(sum(latencies) / len(latencies), 2),
               "max_seconds": max(latencies)}
    print(summary)
    (OUT / "results.json").write_text(json.dumps({"summary": summary, "results": results},
                                                 indent=2, ensure_ascii=False))
    lines = ["| ID | Input (last turn) | Expected | Routed | Result | Time (s) |",
             "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['turns'][-1]} | {'/'.join(r['expected_intent']) if isinstance(r['expected_intent'], list) else r['expected_intent']} | {r['intent']} | "
                     f"{r['status']} | {r['seconds']} |")
    (OUT / "results.md").write_text("\n".join(lines) + "\n\n" + json.dumps(summary) + "\n")


if __name__ == "__main__":
    main()
