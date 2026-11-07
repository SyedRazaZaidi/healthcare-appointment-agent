"""Inject a disruption into the simulated clinic (the 'change event' the agent must replan around).

    python scripts/inject_change_event.py doctor-leave --patient P1001
        the doctor of that patient's latest confirmed appointment goes on leave that day;
        the clinic cancels the appointment (status cancelled_by_clinic, no fee)
    python scripts/inject_change_event.py doctor-leave --doctor D05 --date 2026-11-18
    python scripts/inject_change_event.py race
        arm a one-shot race: the NEXT booking attempt finds its slot just taken by someone else
"""
import _path  # noqa: F401
import argparse

from clinic_agent.clinic_db import apply_doctor_leave, apply_doctor_leave_for_patient, set_fault
from clinic_agent.config import get_settings


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="event", required=True)
    leave = sub.add_parser("doctor-leave")
    leave.add_argument("--patient")
    leave.add_argument("--doctor")
    leave.add_argument("--date")
    sub.add_parser("race")
    args = ap.parse_args()
    db = get_settings().clinic_db

    if args.event == "race":
        set_fault(db, "race_on_first_booking", 1)
        print("armed: next booking attempt will lose a race for its slot")
    elif args.patient:
        print(apply_doctor_leave_for_patient(db, args.patient) or "nothing to cancel for that patient")
    elif args.doctor and args.date:
        print(f"{apply_doctor_leave(db, args.doctor, args.date)} appointment(s) cancelled by the clinic")
    else:
        ap.error("use --patient, or --doctor with --date")


if __name__ == "__main__":
    main()
