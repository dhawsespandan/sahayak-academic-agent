"""Deterministic rule checks (no LLM involved)."""
from datetime import date

from app import rules

TODAY = date(2026, 10, 14)


def test_attendance_borderline_cloud_computing():
    # Aarav, CC: planned 40, conducted 28, attended 22 -> needs 30 of 40, 12 left -> can miss 4
    s = rules.attendance_status(40, 28, 22)
    assert s["required_to_attend_total"] == 30
    assert s["remaining_lectures"] == 12
    assert s["can_still_miss"] == 4
    assert s["current_percent_of_conducted"] == 78.6


def test_attendance_needs_condonation():
    # Isha, ML: planned 45, conducted 31, attended 19 -> best case 33/45 = 73.3% < 75%
    s = rules.attendance_status(45, 31, 19)
    assert s["verdict"] == "needs_condonation"
    assert s["short_by_lectures"] == 1
    assert s["must_attend_for_condonation"] == 11   # needs 30 (65% of 45) -> 11 more


def test_attendance_detained():
    s = rules.attendance_status(45, 40, 20)   # best case 25/45 = 55.6%
    assert s["verdict"] == "detained"


def test_attendance_safe():
    s = rules.attendance_status(45, 32, 30)
    assert s["verdict"] == "safe" and s["can_still_miss"] == 9


def test_reevaluation_window_open_for_ca2():
    w = rules.reevaluation_window(date(2026, 10, 9), TODAY)   # Fri -> 5 working days -> Fri 16 Oct
    assert w["deadline"] == "2026-10-16" and w["open"] is True


def test_reevaluation_window_closed_for_ca1():
    w = rules.reevaluation_window(date(2026, 8, 28), TODAY)
    assert w["deadline"] == "2026-09-04" and w["open"] is False


def test_leave_short_goes_to_mentor():
    r = rules.leave_rules("medical", date(2026, 10, 20), date(2026, 10, 22), TODAY)
    assert r["days"] == 3 and r["approver"] == "mentor" and not r["problems"]
    assert any("medical certificate" in n for n in r["notes"])


def test_leave_long_goes_to_hod():
    r = rules.leave_rules("duty", date(2026, 10, 19), date(2026, 10, 23), TODAY)
    assert r["days"] == 5 and r["approver"] == "hod"


def test_personal_leave_for_today_is_rejected():
    r = rules.leave_rules("personal", TODAY, TODAY, TODAY)
    assert r["problems"]


def test_leave_end_before_start_is_rejected():
    r = rules.leave_rules("personal", date(2026, 10, 22), date(2026, 10, 20), TODAY)
    assert "End date is before start date." in r["problems"]
