"""Scheduling business logic.

``ClinicService`` is plain Python over SQLite. It contains *all* the rules the
clinic enforces (no double booking, new-patient restriction, lead time,
ownership checks...). The MCP server (``mcp_server.py``) is a thin protocol
wrapper around it, which means the rules can be unit-tested without MCP and
the agent cannot bypass them: a rule violation always comes back as a
structured ``{"ok": false, "error_code": ...}`` result.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .clinic_db import connect, slot_id_for  # noqa: F401  (slot_id_for re-exported)

MIN_LEAD_HOURS = 2          # online booking closes 2 hours before the slot
LATE_CANCEL_HOURS = 24      # cancelling <24h before the slot is a "late cancellation"
MAX_ACTIVE_APPOINTMENTS = 3
ALL_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _err(code: str, message: str, **extra: Any) -> dict:
    return {"ok": False, "error_code": code, "message": message, **extra}


class ClinicService:
    def __init__(self, db_path: str | Path, now: datetime):
        self.db_path = Path(db_path)
        self.now = now

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _slot_dt(slot_date: str, start_time: str) -> datetime:
        return datetime.fromisoformat(f"{slot_date}T{start_time}")

    def _view(self, row) -> dict:
        d = date.fromisoformat(row["slot_date"])
        return {
            "slot_id": row["slot_id"],
            "doctor_id": row["doctor_id"],
            "doctor_name": row["doctor_name"],
            "department": row["department"],
            "date": row["slot_date"],
            "weekday": d.strftime("%a"),
            "start_time": row["start_time"],
            "duration_min": row["duration_min"],
        }

    _SLOT_SELECT = (
        "SELECT s.*, d.name AS doctor_name, d.department AS department"
        " FROM slots s JOIN doctors d ON d.doctor_id = s.doctor_id"
    )

    def _bookable_from(self) -> datetime:
        return self.now + timedelta(hours=MIN_LEAD_HOURS)

    # ------------------------------------------------------------------
    # read-only tools
    # ------------------------------------------------------------------
    def list_doctors(self, department: str | None = None) -> dict:
        with connect(self.db_path) as conn:
            if department:
                rows = conn.execute(
                    "SELECT * FROM doctors WHERE lower(department)=lower(?) ORDER BY doctor_id",
                    (department.strip(),),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM doctors ORDER BY doctor_id").fetchall()
            depts = [r["department"] for r in conn.execute("SELECT DISTINCT department FROM doctors")]
        if department and not rows:
            return _err("UNKNOWN_DEPARTMENT", f"No department named '{department}'.", departments=sorted(depts))
        return {
            "ok": True,
            "doctors": [
                {
                    "doctor_id": r["doctor_id"],
                    "name": r["name"],
                    "department": r["department"],
                    "languages": r["languages"],
                    "telehealth_available": bool(r["telehealth"]),
                    "requires_gp_referral": bool(r["requires_referral"]),
                }
                for r in rows
            ],
        }

    def find_available_slots(
        self,
        department: str | None = None,
        doctor_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        part_of_day: str | None = None,
        exclude_weekdays: list[str] | None = None,
        one_per_day: bool = True,
        limit: int = 6,
    ) -> dict:
        if part_of_day not in (None, "morning", "afternoon"):
            return _err("INVALID_ARGUMENT", "part_of_day must be 'morning' or 'afternoon'.")
        try:
            start = date.fromisoformat(date_from) if date_from else self.now.date()
            end = date.fromisoformat(date_to) if date_to else start + timedelta(days=21)
        except ValueError:
            return _err("INVALID_ARGUMENT", "Dates must be ISO format YYYY-MM-DD.")
        if end < start:
            return _err("INVALID_ARGUMENT", "date_to is before date_from.")
        excluded = {w.strip()[:3].title() for w in (exclude_weekdays or [])}
        bad = excluded - set(ALL_WEEKDAYS)
        if bad:
            return _err("INVALID_ARGUMENT", f"Unknown weekday(s): {sorted(bad)}. Use Mon..Sun.")
        limit = max(1, min(int(limit), 10))

        sql = self._SLOT_SELECT + " WHERE s.status='open' AND s.slot_date BETWEEN ? AND ?"
        params: list[Any] = [start.isoformat(), end.isoformat()]
        if doctor_id:
            sql += " AND s.doctor_id = ?"
            params.append(doctor_id)
        if department:
            sql += " AND lower(d.department) = lower(?)"
            params.append(department.strip())
        sql += " ORDER BY s.slot_date, s.start_time, s.doctor_id"
        with connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()

        results, seen = [], set()
        earliest = self._bookable_from()
        for r in rows:
            if self._slot_dt(r["slot_date"], r["start_time"]) < earliest:
                continue
            view = self._view(r)
            if view["weekday"] in excluded:
                continue
            hour = int(r["start_time"][:2])
            if part_of_day == "morning" and hour >= 12:
                continue
            if part_of_day == "afternoon" and hour < 12:
                continue
            key = (r["doctor_id"], r["slot_date"])
            if one_per_day and key in seen:
                continue
            seen.add(key)
            results.append(view)
            if len(results) >= limit:
                break
        return {
            "ok": True,
            "count": len(results),
            "slots": results,
            "note": "No matching open slots; try widening dates or relaxing filters." if not results else "",
        }

    def get_slot(self, slot_id: str) -> dict:
        with connect(self.db_path) as conn:
            row = conn.execute(self._SLOT_SELECT + " WHERE s.slot_id=?", (slot_id,)).fetchone()
        if row is None:
            return _err("UNKNOWN_SLOT", f"No slot with id '{slot_id}'.")
        view = self._view(row)
        view["status"] = row["status"]
        return {"ok": True, "slot": view}

    def list_patient_appointments(self, patient_id: str, include_past: bool = False) -> dict:
        with connect(self.db_path) as conn:
            if conn.execute("SELECT 1 FROM patients WHERE patient_id=?", (patient_id,)).fetchone() is None:
                return _err("UNKNOWN_PATIENT", "Patient not found.")
            rows = conn.execute(
                "SELECT a.*, s.slot_date, s.start_time, s.duration_min, s.doctor_id,"
                " d.name AS doctor_name, d.department AS department"
                " FROM appointments a JOIN slots s ON s.slot_id=a.slot_id"
                " JOIN doctors d ON d.doctor_id=s.doctor_id"
                " WHERE a.patient_id=? ORDER BY s.slot_date, s.start_time",
                (patient_id,),
            ).fetchall()
        out = []
        for r in rows:
            if not include_past and r["slot_date"] < self.now.date().isoformat():
                continue
            item = {
                "appointment_id": r["appointment_id"],
                "status": r["status"],
                "doctor_name": r["doctor_name"],
                "department": r["department"],
                "date": r["slot_date"],
                "weekday": date.fromisoformat(r["slot_date"]).strftime("%a"),
                "start_time": r["start_time"],
                "reason": r["reason"],
            }
            if r["status"] == "cancelled_by_clinic":
                item["action_required"] = (
                    "The clinic cancelled this appointment (" + (r["note"] or "no reason given") + "). "
                    "No fee applies. Offer the patient a new time."
                )
            out.append(item)
        return {"ok": True, "count": len(out), "appointments": out}

    # ------------------------------------------------------------------
    # consequential tools (the agent gates these behind human confirmation)
    # ------------------------------------------------------------------
    def _consume_fault(self, conn, name: str) -> bool:
        row = conn.execute("SELECT remaining FROM fault_injection WHERE name=?", (name,)).fetchone()
        if row and row["remaining"] > 0:
            conn.execute("UPDATE fault_injection SET remaining=remaining-1 WHERE name=?", (name,))
            return True
        return False

    def _alternatives(self, conn, slot_row, limit: int = 3) -> list[dict]:
        """Nearest open slots in the same department (same doctor preferred)."""
        orig = date.fromisoformat(slot_row["slot_date"])
        rows = conn.execute(
            self._SLOT_SELECT + " WHERE s.status='open' AND lower(d.department)=lower(?)"
            " AND s.slot_id != ? ORDER BY s.slot_date, s.start_time",
            (slot_row["department"], slot_row["slot_id"]),
        ).fetchall()
        earliest = self._bookable_from()
        scored = []
        for r in rows:
            if self._slot_dt(r["slot_date"], r["start_time"]) < earliest:
                continue
            gap = abs((date.fromisoformat(r["slot_date"]) - orig).days)
            same_doc = 0 if r["doctor_id"] == slot_row["doctor_id"] else 1
            same_half = 0 if (int(r["start_time"][:2]) < 12) == (int(slot_row["start_time"][:2]) < 12) else 1
            scored.append(((gap, same_doc, same_half, r["slot_date"], r["start_time"]), r))
        scored.sort(key=lambda t: t[0])
        out, seen = [], set()
        for _k, r in scored:
            key = (r["doctor_id"], r["slot_date"])
            if key in seen:
                continue
            seen.add(key)
            out.append(self._view(r))
            if len(out) >= limit:
                break
        return out

    def _validate_bookable(self, conn, patient, slot_row, ignore_appointment_id: str | None = None) -> dict | None:
        """Rules shared by booking and rescheduling. Returns an error dict or None."""
        if self._slot_dt(slot_row["slot_date"], slot_row["start_time"]) < self._bookable_from():
            return _err(
                "TOO_SOON",
                f"Online booking closes {MIN_LEAD_HOURS} hours before the appointment time.",
                alternatives=self._alternatives(conn, slot_row),
            )
        if patient["is_new"] and slot_row["department"] != "General Medicine":
            return _err(
                "NEW_PATIENT_RESTRICTION",
                "New patients must have their first visit with General Medicine.",
                alternatives=[
                    self._view(r)
                    for r in conn.execute(
                        self._SLOT_SELECT + " WHERE s.status='open' AND d.department='General Medicine'"
                        " ORDER BY s.slot_date, s.start_time LIMIT 3"
                    ).fetchall()
                ],
            )
        clash = conn.execute(
            "SELECT 1 FROM appointments a JOIN slots s ON s.slot_id=a.slot_id"
            " WHERE a.patient_id=? AND a.status='confirmed' AND s.slot_date=? AND s.start_time=?"
            " AND a.appointment_id != ?",
            (patient["patient_id"], slot_row["slot_date"], slot_row["start_time"], ignore_appointment_id or ""),
        ).fetchone()
        if clash:
            return _err("PATIENT_TIME_CONFLICT", "The patient already has an appointment at that date and time.")
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM appointments a JOIN slots s ON s.slot_id=a.slot_id"
            " WHERE a.patient_id=? AND a.status='confirmed' AND s.slot_date >= ?"
            " AND a.appointment_id != ?",
            (patient["patient_id"], self.now.date().isoformat(), ignore_appointment_id or ""),
        ).fetchone()["n"]
        if active >= MAX_ACTIVE_APPOINTMENTS:
            return _err(
                "LIMIT_REACHED",
                f"A patient may hold at most {MAX_ACTIVE_APPOINTMENTS} upcoming appointments.",
            )
        return None

    @staticmethod
    def _next_appointment_id(conn) -> str:
        row = conn.execute("SELECT MAX(CAST(SUBSTR(appointment_id, 3) AS INTEGER)) AS m FROM appointments").fetchone()
        return f"A-{(row['m'] or 1000) + 1}"

    def book_appointment(self, patient_id: str, slot_id: str, reason: str = "") -> dict:
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")  # serialise writers: no double booking
            patient = conn.execute("SELECT * FROM patients WHERE patient_id=?", (patient_id,)).fetchone()
            if patient is None:
                return _err("UNKNOWN_PATIENT", "Patient not found.")
            slot = conn.execute(self._SLOT_SELECT + " WHERE s.slot_id=?", (slot_id,)).fetchone()
            if slot is None:
                return _err("UNKNOWN_SLOT", f"No slot with id '{slot_id}'.")
            # Demo fault injection: somebody else grabs the slot a moment earlier.
            if slot["status"] == "open" and self._consume_fault(conn, "race_on_first_booking"):
                conn.execute("UPDATE slots SET status='booked' WHERE slot_id=?", (slot_id,))
                slot = conn.execute(self._SLOT_SELECT + " WHERE s.slot_id=?", (slot_id,)).fetchone()
            if slot["status"] != "open":
                return _err(
                    "SLOT_UNAVAILABLE",
                    "That slot has just been taken or is no longer available.",
                    alternatives=self._alternatives(conn, slot),
                )
            problem = self._validate_bookable(conn, patient, slot)
            if problem:
                return problem
            appt_id = self._next_appointment_id(conn)
            conn.execute("UPDATE slots SET status='booked' WHERE slot_id=?", (slot_id,))
            conn.execute(
                "INSERT INTO appointments(appointment_id, patient_id, slot_id, reason, status, created_at)"
                " VALUES (?,?,?,?, 'confirmed', ?)",
                (appt_id, patient_id, slot_id, (reason or "")[:200], self.now.isoformat()),
            )
            view = self._view(slot)
        return {
            "ok": True,
            "appointment": {
                "appointment_id": appt_id,
                "status": "confirmed",
                "doctor_name": view["doctor_name"],
                "department": view["department"],
                "date": view["date"],
                "weekday": view["weekday"],
                "start_time": view["start_time"],
                "reason": reason,
            },
            "message": "Appointment confirmed.",
        }

    def cancel_appointment(self, patient_id: str, appointment_id: str) -> dict:
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT a.*, s.slot_date, s.start_time FROM appointments a"
                " JOIN slots s ON s.slot_id=a.slot_id WHERE a.appointment_id=? AND a.patient_id=?",
                (appointment_id, patient_id),
            ).fetchone()
            # Same answer for "does not exist" and "belongs to someone else": no information leak.
            if row is None:
                return _err("APPOINTMENT_NOT_FOUND", f"No appointment '{appointment_id}' found for this patient.")
            if row["status"] != "confirmed":
                return _err("INVALID_STATE", f"Appointment is already '{row['status']}' and cannot be cancelled.")
            hours_left = (self._slot_dt(row["slot_date"], row["start_time"]) - self.now).total_seconds() / 3600
            late = hours_left < LATE_CANCEL_HOURS
            conn.execute(
                "UPDATE appointments SET status='cancelled_by_patient', cancelled_at=? WHERE appointment_id=?",
                (self.now.isoformat(), appointment_id),
            )
            conn.execute("UPDATE slots SET status='open' WHERE slot_id=?", (row["slot_id"],))
        result = {
            "ok": True,
            "appointment_id": appointment_id,
            "status": "cancelled_by_patient",
            "late_cancellation": late,
            "message": "Appointment cancelled.",
        }
        if late:
            result["policy_note"] = (
                f"Cancelled less than {LATE_CANCEL_HOURS} hours before the appointment. "
                "A late-cancellation fee may apply - check the cancellation policy."
            )
        return result

    def reschedule_appointment(self, patient_id: str, appointment_id: str, new_slot_id: str) -> dict:
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            patient = conn.execute("SELECT * FROM patients WHERE patient_id=?", (patient_id,)).fetchone()
            if patient is None:
                return _err("UNKNOWN_PATIENT", "Patient not found.")
            old = conn.execute(
                "SELECT * FROM appointments WHERE appointment_id=? AND patient_id=?",
                (appointment_id, patient_id),
            ).fetchone()
            if old is None:
                return _err("APPOINTMENT_NOT_FOUND", f"No appointment '{appointment_id}' found for this patient.")
            # A clinic-cancelled appointment may be re-booked through this tool (no fee, priority rebooking).
            if old["status"] not in ("confirmed", "cancelled_by_clinic"):
                return _err("INVALID_STATE", f"Appointment is '{old['status']}' and cannot be rescheduled.")
            new = conn.execute(self._SLOT_SELECT + " WHERE s.slot_id=?", (new_slot_id,)).fetchone()
            if new is None:
                return _err("UNKNOWN_SLOT", f"No slot with id '{new_slot_id}'.")
            if new["status"] != "open":
                return _err(
                    "SLOT_UNAVAILABLE",
                    "That slot is no longer available.",
                    alternatives=self._alternatives(conn, new),
                )
            problem = self._validate_bookable(conn, patient, new, ignore_appointment_id=appointment_id)
            if problem:
                return problem
            new_id = self._next_appointment_id(conn)
            if old["status"] == "confirmed":
                conn.execute("UPDATE slots SET status='open' WHERE slot_id=?", (old["slot_id"],))
            conn.execute(
                "UPDATE appointments SET status='rescheduled', note=? WHERE appointment_id=?",
                (f"moved to {new_id}", appointment_id),
            )
            conn.execute("UPDATE slots SET status='booked' WHERE slot_id=?", (new_slot_id,))
            conn.execute(
                "INSERT INTO appointments(appointment_id, patient_id, slot_id, reason, status, created_at, note)"
                " VALUES (?,?,?,?, 'confirmed', ?, ?)",
                (new_id, patient_id, new_slot_id, old["reason"], self.now.isoformat(), f"rescheduled from {appointment_id}"),
            )
            view = self._view(new)
        return {
            "ok": True,
            "old_appointment_id": appointment_id,
            "appointment": {
                "appointment_id": new_id,
                "status": "confirmed",
                "doctor_name": view["doctor_name"],
                "department": view["department"],
                "date": view["date"],
                "weekday": view["weekday"],
                "start_time": view["start_time"],
                "reason": old["reason"],
            },
            "message": "Appointment rescheduled.",
        }
