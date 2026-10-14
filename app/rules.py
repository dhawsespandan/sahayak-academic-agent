"""Deterministic academic rules.

Anything that is arithmetic or a hard policy limit is computed here in plain
Python instead of asking the LLM, so the numbers are always exact and testable.
The constants mirror the sample regulations in data/docs_src.
"""
import math
from datetime import date, timedelta

MIN_ATTENDANCE = 0.75
CONDONATION_FLOOR = 0.65
MENTOR_LEAVE_LIMIT_DAYS = 3        # leave longer than this goes to the HoD
MEDICAL_CERT_AFTER_DAYS = 2        # medical leave longer than this needs a certificate
REEVAL_WINDOW_WORKING_DAYS = 5


def attendance_status(planned: int, conducted: int, attended: int) -> dict:
    """How many of the remaining lectures a student can still miss.

    Attendance is judged on *planned* lectures (Attendance Policy, sec. 3), so
    the student must finish with at least ceil(0.75 * planned) lectures attended.
    """
    remaining = planned - conducted
    required = math.ceil(MIN_ATTENDANCE * planned)
    best_possible = attended + remaining
    can_miss = best_possible - required
    status = {
        "current_percent_of_conducted": round(100 * attended / conducted, 1) if conducted else 0.0,
        "planned": planned,
        "conducted": conducted,
        "attended": attended,
        "remaining_lectures": remaining,
        "required_to_attend_total": required,
        "percent_if_all_remaining_attended": round(100 * best_possible / planned, 1),
    }
    if can_miss >= 0:
        status["can_still_miss"] = can_miss
        status["verdict"] = "safe" if can_miss > 2 else "borderline"
        return status

    # 75% is out of reach: check whether condonation (down to 65%) is still possible.
    floor_required = math.ceil(CONDONATION_FLOOR * planned)
    status["can_still_miss"] = 0
    status["short_by_lectures"] = -can_miss
    if best_possible >= floor_required:
        status["verdict"] = "needs_condonation"
        status["must_attend_for_condonation"] = max(0, floor_required - attended)
    else:
        status["verdict"] = "detained"
    return status


def add_working_days(start: date, n: int) -> date:
    """Date that is n working days (Mon-Fri) after start."""
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def reevaluation_window(published_on: date, today: date) -> dict:
    deadline = add_working_days(published_on, REEVAL_WINDOW_WORKING_DAYS)
    return {"published_on": published_on.isoformat(), "deadline": deadline.isoformat(),
            "open": today <= deadline}


def leave_rules(leave_type: str, start: date, end: date, today: date) -> dict:
    """Validates a leave request and decides who must approve it."""
    days = (end - start).days + 1
    problems, notes = [], []
    if days <= 0:
        problems.append("End date is before start date.")
    if leave_type == "personal" and start <= today:
        problems.append("Personal leave must be applied at least 1 working day in advance.")
    if leave_type == "medical" and days > MEDICAL_CERT_AFTER_DAYS:
        notes.append("A medical certificate is required for medical leave of more than 2 days.")
    if leave_type == "duty":
        notes.append("Attach the official letter from the event organiser.")
    approver = "hod" if days > MENTOR_LEAVE_LIMIT_DAYS else "mentor"
    if approver == "hod":
        notes.append("Leave of more than 3 days is forwarded by the mentor to the HoD.")
    return {"days": days, "approver": approver, "problems": problems, "notes": notes}
