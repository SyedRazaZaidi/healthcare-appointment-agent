"""MCP server: the clinic scheduling system exposed as Model Context Protocol tools.

Run standalone to inspect it (e.g. with the MCP Inspector)::

    python -m clinic_agent.mcp_server

It speaks MCP over **stdio**, so the agent's MCP client launches it as a
subprocess. IMPORTANT: with the stdio transport, stdout carries the protocol
messages, so this module must never print to stdout - all logging goes to stderr.

The server holds no business rules itself; it validates nothing beyond types
and delegates to :class:`ClinicService`, which enforces the clinic's rules.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from .clinic_tools import ClinicService
from .config import get_settings

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="[mcp-server] %(message)s")
log = logging.getLogger("clinic-mcp")

_settings = get_settings()
_db = os.environ.get("CLINIC_DB_PATH", str(_settings.clinic_db))
_now = datetime.fromisoformat(os.environ.get("CLINIC_NOW", _settings.clinic_now.isoformat()))
service = ClinicService(_db, _now)

mcp = FastMCP("clinic-scheduler")


def _safe(fn, **kwargs) -> dict[str, Any]:
    """Run a service call; convert unexpected exceptions into a structured error
    so one bad call never crashes the server process."""
    try:
        result = fn(**kwargs)
        log.info("%s -> ok=%s", fn.__name__, result.get("ok"))
        return result
    except Exception as exc:  # noqa: BLE001
        log.exception("tool %s failed", fn.__name__)
        return {"ok": False, "error_code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def list_doctors(department: str | None = None) -> dict:
    """List the clinic's doctors, optionally filtered by department.

    Departments: General Medicine, Pediatrics, Cardiology, Dermatology, Orthopedics.
    Each doctor shows languages, whether telehealth is offered and whether a GP referral is required.
    """
    return _safe(service.list_doctors, department=department)


@mcp.tool()
def find_available_slots(
    department: str | None = None,
    doctor_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    part_of_day: str | None = None,
    exclude_weekdays: list[str] | None = None,
    one_per_day: bool = True,
    limit: int = 6,
) -> dict:
    """Find open appointment slots.

    Args:
        department: e.g. 'Dermatology'. Optional.
        doctor_id: e.g. 'D05'. Optional.
        date_from: first date to consider, ISO YYYY-MM-DD. Defaults to today.
        date_to: last date to consider, ISO YYYY-MM-DD. Defaults to 3 weeks after date_from.
        part_of_day: 'morning' (before 12:00) or 'afternoon'. Optional.
        exclude_weekdays: weekdays to skip, e.g. ['Mon'].
        one_per_day: if true (default) return only the earliest slot per doctor per day, giving
            variety across days; set false to see all times on a given day.
        limit: maximum number of slots (1-10).
    """
    return _safe(
        service.find_available_slots,
        department=department, doctor_id=doctor_id, date_from=date_from, date_to=date_to,
        part_of_day=part_of_day, exclude_weekdays=exclude_weekdays, one_per_day=one_per_day, limit=limit,
    )


@mcp.tool()
def get_slot(slot_id: str) -> dict:
    """Get the details and current status (open/booked/blocked) of one slot by its id."""
    return _safe(service.get_slot, slot_id=slot_id)


@mcp.tool()
def list_my_appointments(patient_id: str, include_past: bool = False) -> dict:
    """List the signed-in patient's appointments (upcoming by default), including any the clinic cancelled."""
    return _safe(service.list_patient_appointments, patient_id=patient_id, include_past=include_past)


@mcp.tool()
def book_appointment(patient_id: str, slot_id: str, reason: str = "") -> dict:
    """Book an open slot for the patient. CONSEQUENTIAL: changes the schedule.

    The clinic rules (no double booking, new-patient restriction, 2-hour cut-off,
    max 3 upcoming appointments) are enforced here. On failure the result has
    ok=false, an error_code and often alternative slots.
    """
    return _safe(service.book_appointment, patient_id=patient_id, slot_id=slot_id, reason=reason)


@mcp.tool()
def cancel_appointment(patient_id: str, appointment_id: str) -> dict:
    """Cancel one of the patient's confirmed appointments. CONSEQUENTIAL.

    The result says whether this was a late cancellation (less than 24 hours before the slot).
    """
    return _safe(service.cancel_appointment, patient_id=patient_id, appointment_id=appointment_id)


@mcp.tool()
def reschedule_appointment(patient_id: str, appointment_id: str, new_slot_id: str) -> dict:
    """Move an appointment to a different open slot. CONSEQUENTIAL.

    Also used to re-book an appointment that the clinic itself cancelled. The old
    appointment is closed and a new appointment id is issued.
    """
    return _safe(
        service.reschedule_appointment,
        patient_id=patient_id, appointment_id=appointment_id, new_slot_id=new_slot_id,
    )


if __name__ == "__main__":
    log.info("starting clinic-scheduler MCP server (stdio) db=%s now=%s", _db, _now.isoformat())
    mcp.run(transport="stdio")
