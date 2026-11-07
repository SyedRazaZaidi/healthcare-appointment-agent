"""Simulated clinic back-end: SQLite schema, deterministic seed data and
"change events" (the disruptions the agent must replan around).

All data is synthetic. This module is the *world* the agent operates in; the
agent never imports it for scheduling - it reaches it only through the MCP
server (``mcp_server.py``). The only exception is :func:`get_patient`, which
plays the role of the clinic's login/authentication system.
"""
from __future__ import annotations

import random
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS doctors (
    doctor_id        TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    department       TEXT NOT NULL,
    languages        TEXT NOT NULL,
    telehealth       INTEGER NOT NULL DEFAULT 0,
    requires_referral INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS patients (
    patient_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    is_new     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS slots (
    slot_id      TEXT PRIMARY KEY,           -- e.g. D05-20261118-0930
    doctor_id    TEXT NOT NULL REFERENCES doctors(doctor_id),
    slot_date    TEXT NOT NULL,              -- ISO date
    start_time   TEXT NOT NULL,              -- HH:MM
    duration_min INTEGER NOT NULL DEFAULT 30,
    status       TEXT NOT NULL CHECK (status IN ('open','booked','blocked'))
);
CREATE INDEX IF NOT EXISTS idx_slots_lookup ON slots(status, slot_date, doctor_id);
CREATE TABLE IF NOT EXISTS appointments (
    appointment_id TEXT PRIMARY KEY,         -- e.g. A-1004
    patient_id     TEXT NOT NULL REFERENCES patients(patient_id),
    slot_id        TEXT NOT NULL REFERENCES slots(slot_id),
    reason         TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL CHECK (status IN
                   ('confirmed','cancelled_by_patient','cancelled_by_clinic','rescheduled')),
    created_at     TEXT NOT NULL,
    cancelled_at   TEXT,
    note           TEXT
);
CREATE TABLE IF NOT EXISTS fault_injection (
    name      TEXT PRIMARY KEY,
    remaining INTEGER NOT NULL
);
"""

# (id, name, department, languages, telehealth, requires_referral, working weekdays)
# weekdays: 0=Mon .. 5=Sat
DOCTORS = [
    ("D01", "Dr. Amina Rahman", "General Medicine", "English, Urdu", 1, 0, {0, 1, 2, 3, 4}),
    ("D02", "Dr. Daniel Okafor", "General Medicine", "English", 1, 0, {0, 2, 4, 5}),
    ("D03", "Dr. Sofia Marin", "Pediatrics", "English, Spanish", 1, 0, {0, 1, 2, 3}),
    ("D04", "Dr. Hiro Tanaka", "Cardiology", "English, Japanese", 0, 1, {1, 3}),
    ("D05", "Dr. Leila Haddad", "Dermatology", "English, Arabic", 1, 0, {0, 2, 4}),
    ("D06", "Dr. Priya Nair", "Orthopedics", "English, Hindi", 0, 0, {1, 3, 4}),
]

PATIENTS = [
    ("P1001", "Sara Ahmed", 0),
    ("P1002", "Omar Siddiqui", 0),
    ("P1003", "Maria Lopez", 1),  # new patient: restricted to General Medicine
]

WEEKDAY_TIMES = [f"{h:02d}:{m:02d}" for h in (9, 10, 11) for m in (0, 30)] + [
    f"{h:02d}:{m:02d}" for h in (14, 15, 16) for m in (0, 30)
]
SATURDAY_TIMES = [f"{h:02d}:{m:02d}" for h in (9, 10, 11, 12) for m in (0, 30)]


@contextmanager
def connect(db_path: str | Path):
    """Open a connection with row access by name and foreign keys on."""
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def slot_id_for(doctor_id: str, day: date, start: str) -> str:
    return f"{doctor_id}-{day:%Y%m%d}-{start.replace(':', '')}"


def seed(db_path: str | Path, now: datetime, days_ahead: int = 21, rng_seed: int = 2026) -> None:
    """(Re)create the database with deterministic synthetic data.

    Determinism matters: the demo and the tests must see the same world on
    every machine, so a fixed RNG seed decides which filler slots are taken.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rng = random.Random(rng_seed)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        for did, name, dept, langs, tele, ref, _ in DOCTORS:
            conn.execute(
                "INSERT INTO doctors VALUES (?,?,?,?,?,?)", (did, name, dept, langs, tele, ref)
            )
        conn.executemany("INSERT INTO patients VALUES (?,?,?)", PATIENTS)

        first_day = now.date() + timedelta(days=1)
        for offset in range(days_ahead):
            day = first_day + timedelta(days=offset)
            for did, _n, _d, _l, _t, _r, weekdays in DOCTORS:
                if day.weekday() not in weekdays:
                    continue
                times = SATURDAY_TIMES if day.weekday() == 5 else WEEKDAY_TIMES
                for start in times:
                    status = "booked" if rng.random() < 0.35 else "open"
                    conn.execute(
                        "INSERT INTO slots VALUES (?,?,?,?,?,?)",
                        (slot_id_for(did, day, start), did, day.isoformat(), start, 30, status),
                    )

        # Two pre-existing confirmed appointments (used by tests and the demo).
        _force_appointment(conn, "A-1001", "P1001", "D01", date(2026, 11, 12), "09:30", "Annual check-up", now)
        _force_appointment(conn, "A-1002", "P1002", "D04", date(2026, 11, 12), "14:00", "Cardiology follow-up", now)


def _force_appointment(conn, appt_id, patient_id, doctor_id, day, start, reason, now) -> None:
    sid = slot_id_for(doctor_id, day, start)
    row = conn.execute("SELECT 1 FROM slots WHERE slot_id=?", (sid,)).fetchone()
    if row is None:
        return  # slot outside the seeded horizon (e.g. a different CLINIC_NOW)
    conn.execute("UPDATE slots SET status='booked' WHERE slot_id=?", (sid,))
    conn.execute(
        "INSERT INTO appointments(appointment_id, patient_id, slot_id, reason, status, created_at)"
        " VALUES (?,?,?,?, 'confirmed', ?)",
        (appt_id, patient_id, sid, reason, now.isoformat()),
    )


# --------------------------------------------------------------------------
# "Login" helper used by the CLI/runtime (simulated authentication)
# --------------------------------------------------------------------------
def get_patient(db_path: str | Path, patient_id: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM patients WHERE patient_id=?", (patient_id,)).fetchone()
    return dict(row) if row else None


def list_patients(db_path: str | Path) -> list[dict]:
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute("SELECT patient_id, name, is_new FROM patients ORDER BY patient_id")]


# --------------------------------------------------------------------------
# Change events (fault injection / disruptions)
# --------------------------------------------------------------------------
def set_fault(db_path: str | Path, name: str, remaining: int = 1) -> None:
    """Arm a one-shot fault. ``race_on_first_booking``: the next booking attempt
    finds its slot just taken by somebody else (a classic concurrency race)."""
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO fault_injection(name, remaining) VALUES (?,?)"
            " ON CONFLICT(name) DO UPDATE SET remaining=excluded.remaining",
            (name, remaining),
        )


def apply_doctor_leave(db_path: str | Path, doctor_id: str, day: str, note: str = "Doctor on leave") -> int:
    """Block a doctor's whole day and cancel their confirmed appointments.
    Returns the number of appointments cancelled by the clinic."""
    with connect(db_path) as conn:
        slot_rows = conn.execute(
            "SELECT slot_id FROM slots WHERE doctor_id=? AND slot_date=?", (doctor_id, day)
        ).fetchall()
        cancelled = 0
        for r in slot_rows:
            cur = conn.execute(
                "UPDATE appointments SET status='cancelled_by_clinic', cancelled_at=?, note=?"
                " WHERE slot_id=? AND status='confirmed'",
                (datetime.now().isoformat(timespec="seconds"), note, r["slot_id"]),
            )
            cancelled += cur.rowcount
            conn.execute("UPDATE slots SET status='blocked' WHERE slot_id=?", (r["slot_id"],))
    return cancelled


def apply_doctor_leave_for_patient(db_path: str | Path, patient_id: str) -> dict | None:
    """Demo helper: the doctor of the patient's *latest* confirmed appointment
    goes on leave that day. Returns what happened (or None if nothing to do)."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT a.appointment_id, s.doctor_id, s.slot_date, d.name AS doctor_name"
            " FROM appointments a JOIN slots s ON s.slot_id=a.slot_id"
            " JOIN doctors d ON d.doctor_id=s.doctor_id"
            " WHERE a.patient_id=? AND a.status='confirmed'"
            " ORDER BY s.slot_date DESC, s.start_time DESC LIMIT 1",
            (patient_id,),
        ).fetchone()
    if row is None:
        return None
    n = apply_doctor_leave(db_path, row["doctor_id"], row["slot_date"])
    return {
        "appointment_id": row["appointment_id"],
        "doctor": row["doctor_name"],
        "date": row["slot_date"],
        "appointments_cancelled": n,
    }
