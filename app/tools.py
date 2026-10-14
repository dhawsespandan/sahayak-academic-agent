"""Tools the personal-data agent can call.

The student's PRN is injected from the graph state (InjectedState), so it is
NOT a parameter the LLM can fill in. Even a prompt like "show marks of PRN X"
cannot make these tools read another student's records.
"""
from datetime import date
from typing import Annotated, Optional

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app import config, db, rag, rules


def _match_course(course: Optional[str]) -> Optional[str]:
    """Maps 'ML', 'machine learning', 'cloud' etc. to a course code."""
    if not course:
        return None
    c = course.strip().lower()
    for row in db.get_courses():
        if c in (row["code"].lower(), row["name"].lower()) or c in row["name"].lower():
            return row["code"]
    return "UNKNOWN"


@tool
def get_my_attendance(prn: Annotated[str, InjectedState("prn")],
                      course: Optional[str] = None) -> dict:
    """Get the logged-in student's attendance for one course (code or name) or all courses,
    including how many more lectures they can miss and still reach 75 percent."""
    code = _match_course(course)
    if code == "UNKNOWN":
        return {"error": f"No course matches '{course}'.",
                "valid_courses": [f"{c['code']} ({c['name']})" for c in db.get_courses()]}
    out = []
    for row in db.get_attendance(prn):
        if code and row["course_code"] != code:
            continue
        status = rules.attendance_status(row["planned_lectures"], row["conducted"], row["attended"])
        out.append({"course": f"{row['course_code']} - {row['course_name']}",
                    "as_of": row["updated_on"], **status})
    return {"attendance": out}


@tool
def get_my_ca_marks(prn: Annotated[str, InjectedState("prn")],
                    course: Optional[str] = None) -> dict:
    """Get the logged-in student's continuous assessment (CA1, CA2, CA3) marks for one course or all
    courses, with the publication date and whether the re-evaluation window is still open."""
    code = _match_course(course)
    if code == "UNKNOWN":
        return {"error": f"No course matches '{course}'.",
                "valid_courses": [f"{c['code']} ({c['name']})" for c in db.get_courses()]}
    today = config.today()
    out = []
    for row in db.get_ca_marks(prn):
        if code and row["course_code"] != code:
            continue
        window = rules.reevaluation_window(date.fromisoformat(row["published_on"]), today)
        out.append({"course": f"{row['course_code']} - {row['course_name']}",
                    "component": row["component"], "marks": row["marks"],
                    "max_marks": row["max_marks"], "published_on": row["published_on"],
                    "reevaluation_deadline": window["deadline"],
                    "reevaluation_open": window["open"]})
    return {"ca_marks": out,
            "note": ("CA3 (20 marks, mini project) is scheduled later in the semester and has not "
                     "been held yet for anyone. It is NOT a missing submission and does not affect "
                     "exam eligibility today.")}


@tool
def get_exam_schedule(course: Optional[str] = None) -> dict:
    """Get the end-semester examination timetable (date, time, venue) for one course or all."""
    code = _match_course(course)
    rows = [r for r in db.get_exams() if not code or code == "UNKNOWN" or r["course_code"] == code]
    return {"exams": rows}


@tool
def get_my_requests(prn: Annotated[str, InjectedState("prn")]) -> dict:
    """List the leave / re-evaluation / certificate requests the logged-in student has submitted,
    with their reference numbers and status."""
    return {"requests": [{k: r[k] for k in ("ref", "type", "status", "created_at")}
                         for r in db.get_requests(prn)]}


@tool
def search_regulations(query: str) -> dict:
    """Search the institute's academic regulations (attendance, leave, assessment, exams,
    certificates). Use it when a personal question also depends on a rule."""
    hits = rag.search(query, k=3)
    return {"results": [{"source": f"{h['title']}, page {h['page']}", "text": h["text"],
                         "score": h["score"]} for h in hits]}


PERSONAL_TOOLS = [get_my_attendance, get_my_ca_marks, get_exam_schedule, get_my_requests,
                  search_regulations]
