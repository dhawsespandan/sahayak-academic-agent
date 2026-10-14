"""SQLite store for the (sample) student records the agent can look up.

Data is synthetic: 30 students of Division C with attendance, CA marks and an
exam timetable. Seeding is deterministic (fixed random seed) so tests can rely
on exact numbers.
"""
import json
import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS faculty (
    id TEXT PRIMARY KEY, name TEXT, email TEXT, role TEXT
);
CREATE TABLE IF NOT EXISTS courses (
    code TEXT PRIMARY KEY, name TEXT, planned_lectures INTEGER, faculty_id TEXT
);
CREATE TABLE IF NOT EXISTS students (
    prn TEXT PRIMARY KEY, name TEXT, email TEXT, division TEXT, mentor_id TEXT
);
CREATE TABLE IF NOT EXISTS attendance (
    prn TEXT, course_code TEXT, conducted INTEGER, attended INTEGER, updated_on TEXT,
    PRIMARY KEY (prn, course_code)
);
CREATE TABLE IF NOT EXISTS ca_marks (
    prn TEXT, course_code TEXT, component TEXT, marks REAL, max_marks REAL, published_on TEXT,
    PRIMARY KEY (prn, course_code, component)
);
CREATE TABLE IF NOT EXISTS exams (
    course_code TEXT PRIMARY KEY, exam_date TEXT, start_time TEXT, venue TEXT
);
CREATE TABLE IF NOT EXISTS requests (
    ref TEXT PRIMARY KEY, prn TEXT, type TEXT, details TEXT, approver_id TEXT,
    status TEXT, dispatch_status TEXT, created_at TEXT
);
"""

FACULTY = [
    ("F01", "Prof. Meera Joshi", "meera.joshi@demo-institute.edu", "mentor"),
    ("F02", "Dr. Rakesh Iyer", "rakesh.iyer@demo-institute.edu", "hod"),
    ("F03", "Prof. Sneha Patil", "sneha.patil@demo-institute.edu", "course"),
    ("F04", "Dr. Arjun Rao", "arjun.rao@demo-institute.edu", "course"),
    ("F05", "Prof. Kavita Deshmukh", "kavita.deshmukh@demo-institute.edu", "course"),
]

COURSES = [
    ("AAI", "Agentic AI", 45, "F03"),
    ("ML", "Machine Learning", 45, "F04"),
    ("CC", "Cloud Computing", 40, "F05"),
    ("SE", "Software Engineering", 40, "F03"),
    ("DV", "Data Visualization", 30, "F04"),
]

# Lectures conducted till the last attendance update (2026-10-12).
CONDUCTED = {"AAI": 32, "ML": 31, "CC": 28, "SE": 27, "DV": 21}
ATTENDANCE_UPDATED = "2026-10-12"

EXAMS = [
    ("AAI", "2026-11-23", "10:00", "Hall A-201"),
    ("ML", "2026-11-25", "10:00", "Hall A-201"),
    ("CC", "2026-11-27", "14:00", "Hall B-104"),
    ("SE", "2026-11-30", "10:00", "Hall B-104"),
    ("DV", "2026-12-02", "14:00", "Lab C-3"),
]

CA_COMPONENTS = [("CA1", 10, "2026-08-28"), ("CA2", 10, "2026-10-09")]  # CA3 not yet held

FIRST = ["Aarav", "Isha", "Rohan", "Ananya", "Kabir", "Diya", "Vihaan", "Saanvi", "Arjun", "Myra",
         "Reyansh", "Aditi", "Krish", "Tanvi", "Yash", "Riya", "Atharv", "Pooja", "Neel", "Shreya",
         "Omkar", "Gauri", "Siddharth", "Nidhi", "Pranav", "Sakshi", "Harsh", "Mitali", "Varun", "Kriti"]
LAST = ["Mehta", "Kulkarni", "Shah", "Nair", "Singh", "Patel", "Deshpande", "Gupta", "Pawar", "Reddy"]

# Hand-set records for the demo students so that every interesting case exists.
DEMO_ATTENDANCE = {
    "22070126001": {"AAI": 30, "ML": 29, "CC": 22, "SE": 25, "DV": 20},  # CC is borderline
    "22070126002": {"AAI": 26, "ML": 19, "CC": 26, "SE": 24, "DV": 15},  # ML cannot reach 75%
}


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(reset: bool = False) -> None:
    with session() as conn:
        if reset:
            for t in ["faculty", "courses", "students", "attendance", "ca_marks", "exams", "requests"]:
                conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]:
            return
        _seed(conn)


def _seed(conn: sqlite3.Connection) -> None:
    rng = random.Random(42)
    conn.executemany("INSERT INTO faculty VALUES (?,?,?,?)", FACULTY)
    conn.executemany("INSERT INTO courses VALUES (?,?,?,?)", COURSES)
    conn.executemany("INSERT INTO exams VALUES (?,?,?,?)", EXAMS)
    for i in range(30):
        prn = f"22070126{i + 1:03d}"
        name = f"{FIRST[i]} {LAST[i % len(LAST)]}"
        email = f"{FIRST[i].lower()}.{LAST[i % len(LAST)].lower()}@student.demo-institute.edu"
        conn.execute("INSERT INTO students VALUES (?,?,?,?,?)", (prn, name, email, "C", "F01"))
        for code, _, _, _ in COURSES:
            conducted = CONDUCTED[code]
            attended = DEMO_ATTENDANCE.get(prn, {}).get(code)
            if attended is None:
                attended = min(conducted, int(conducted * rng.uniform(0.62, 1.0)))
            conn.execute("INSERT INTO attendance VALUES (?,?,?,?,?)",
                         (prn, code, conducted, attended, ATTENDANCE_UPDATED))
            for comp, max_marks, published in CA_COMPONENTS:
                marks = round(rng.uniform(3.5, 10.0) * 2) / 2
                conn.execute("INSERT INTO ca_marks VALUES (?,?,?,?,?,?)",
                             (prn, code, comp, marks, max_marks, published))


# ---------- queries used by the agent tools ----------

def get_student(prn: str):
    with session() as conn:
        row = conn.execute("SELECT * FROM students WHERE prn=?", (prn,)).fetchone()
        return dict(row) if row else None


def list_students():
    with session() as conn:
        return [dict(r) for r in conn.execute("SELECT prn, name FROM students ORDER BY prn")]


def get_courses():
    with session() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM courses")]


def get_attendance(prn: str):
    with session() as conn:
        rows = conn.execute(
            """SELECT a.course_code, c.name AS course_name, c.planned_lectures, a.conducted,
                      a.attended, a.updated_on
               FROM attendance a JOIN courses c ON c.code = a.course_code
               WHERE a.prn=? ORDER BY a.course_code""", (prn,))
        return [dict(r) for r in rows]


def get_ca_marks(prn: str):
    with session() as conn:
        rows = conn.execute(
            """SELECT m.course_code, c.name AS course_name, m.component, m.marks, m.max_marks,
                      m.published_on
               FROM ca_marks m JOIN courses c ON c.code = m.course_code
               WHERE m.prn=? ORDER BY m.course_code, m.component""", (prn,))
        return [dict(r) for r in rows]


def get_exams():
    with session() as conn:
        rows = conn.execute(
            """SELECT e.course_code, c.name AS course_name, e.exam_date, e.start_time, e.venue
               FROM exams e JOIN courses c ON c.code = e.course_code ORDER BY e.exam_date""")
        return [dict(r) for r in rows]


def get_faculty(fid: str):
    with session() as conn:
        row = conn.execute("SELECT * FROM faculty WHERE id=?", (fid,)).fetchone()
        return dict(row) if row else None


def get_hod():
    with session() as conn:
        return dict(conn.execute("SELECT * FROM faculty WHERE role='hod'").fetchone())


def save_request(ref, prn, rtype, details: dict, approver_id, status, dispatch_status):
    with session() as conn:
        conn.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?)",
                     (ref, prn, rtype, json.dumps(details), approver_id, status, dispatch_status,
                      datetime.now().isoformat(timespec="seconds")))


def get_requests(prn: str):
    with session() as conn:
        rows = conn.execute("SELECT * FROM requests WHERE prn=? ORDER BY created_at DESC", (prn,))
        out = []
        for r in rows:
            d = dict(r)
            d["details"] = json.loads(d["details"])
            out.append(d)
        return out


def next_request_ref() -> str:
    with session() as conn:
        n = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    return f"SAH-{datetime.now():%Y%m%d}-{n + 1:04d}"
