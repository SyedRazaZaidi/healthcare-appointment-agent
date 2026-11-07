"""Tests for the scheduling rules (the logic behind the MCP tools)."""
import unittest

from helpers import NOW, World

from clinic_agent.clinic_db import apply_doctor_leave, apply_doctor_leave_for_patient, set_fault


class SlotSearchTests(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.s = self.w.service

    def tearDown(self):
        self.w.close()

    def test_filters_department_morning_and_excluded_weekday(self):
        res = self.s.find_available_slots(department="Dermatology", part_of_day="morning",
                                          exclude_weekdays=["Mon"], date_from="2026-11-16", date_to="2026-11-20")
        self.assertTrue(res["ok"])
        self.assertGreater(res["count"], 0)
        for slot in res["slots"]:
            self.assertEqual(slot["department"], "Dermatology")
            self.assertLess(int(slot["start_time"][:2]), 12)
            self.assertNotEqual(slot["weekday"], "Mon")

    def test_one_per_day_gives_variety(self):
        res = self.s.find_available_slots(department="General Medicine", limit=10)
        keys = [(x["doctor_id"], x["date"]) for x in res["slots"]]
        self.assertEqual(len(keys), len(set(keys)))
        all_times = self.s.find_available_slots(doctor_id="D01", date_from="2026-11-10", date_to="2026-11-10",
                                                one_per_day=False, limit=10)
        self.assertGreater(all_times["count"], 1)

    def test_invalid_arguments_return_structured_errors(self):
        self.assertEqual(self.s.find_available_slots(part_of_day="night")["error_code"], "INVALID_ARGUMENT")
        self.assertEqual(self.s.find_available_slots(date_from="tomorrow")["error_code"], "INVALID_ARGUMENT")
        self.assertEqual(self.s.find_available_slots(exclude_weekdays=["Funday"])["error_code"], "INVALID_ARGUMENT")
        self.assertEqual(self.s.list_doctors("Astrology")["error_code"], "UNKNOWN_DEPARTMENT")

    def test_lead_time_hides_slots_starting_within_two_hours(self):
        # clinic time is Mon 15:00; nothing before 17:00 today may be offered
        res = self.s.find_available_slots(date_from="2026-11-09", date_to="2026-11-09")
        self.assertEqual(res["count"], 0)

    def test_cardiology_flags_referral(self):
        doctors = self.s.list_doctors("Cardiology")["doctors"]
        self.assertTrue(doctors[0]["requires_gp_referral"])


class BookingRuleTests(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.s = self.w.service

    def tearDown(self):
        self.w.close()

    def test_successful_booking_marks_slot_and_lists_appointment(self):
        slot = self.w.first_open_slot("Dermatology")
        res = self.s.book_appointment("P1001", slot["slot_id"], "skin check")
        self.assertTrue(res["ok"])
        self.assertEqual(res["appointment"]["doctor_name"], slot["doctor_name"])
        self.assertEqual(self.w.slot_status(slot["slot_id"]), "booked")
        ids = [a["appointment_id"] for a in self.s.list_patient_appointments("P1001")["appointments"]]
        self.assertIn(res["appointment"]["appointment_id"], ids)

    def test_double_booking_the_same_slot_is_rejected_with_alternatives(self):
        slot = self.w.first_open_slot("Dermatology")
        self.assertTrue(self.s.book_appointment("P1001", slot["slot_id"])["ok"])
        res = self.s.book_appointment("P1002", slot["slot_id"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_code"], "SLOT_UNAVAILABLE")
        self.assertTrue(res["alternatives"])
        self.assertNotIn(slot["slot_id"], [a["slot_id"] for a in res["alternatives"]])

    def test_new_patient_cannot_book_a_specialist(self):
        slot = self.w.first_open_slot("Dermatology")
        res = self.s.book_appointment("P1003", slot["slot_id"])
        self.assertEqual(res["error_code"], "NEW_PATIENT_RESTRICTION")
        self.assertTrue(all(a["department"] == "General Medicine" for a in res["alternatives"]))
        gp = self.w.first_open_slot("General Medicine")
        self.assertTrue(self.s.book_appointment("P1003", gp["slot_id"])["ok"])

    def test_patient_cannot_hold_two_appointments_at_the_same_time(self):
        # A-1001 is P1001 with D01 at 2026-11-12 09:30; find another doctor open at the same time
        same_time = self.s.find_available_slots(date_from="2026-11-12", date_to="2026-11-12", one_per_day=False,
                                                limit=10)["slots"]
        clash = [x for x in same_time if x["start_time"] == "09:30"]
        if not clash:
            self.skipTest("no other doctor open at 09:30 in this seed")
        self.assertEqual(self.s.book_appointment("P1001", clash[0]["slot_id"])["error_code"], "PATIENT_TIME_CONFLICT")

    def test_max_three_upcoming_appointments(self):
        booked = 1  # P1001 already has A-1001
        for day in ("2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"):
            slot = self.s.find_available_slots(department="General Medicine", date_from=day, date_to=day)["slots"][0]
            res = self.s.book_appointment("P1001", slot["slot_id"])
            if booked < 3:
                self.assertTrue(res["ok"], res)
                booked += 1
            else:
                self.assertEqual(res["error_code"], "LIMIT_REACHED")
                return
        self.fail("limit was never reached")

    def test_unknown_ids_are_handled(self):
        self.assertEqual(self.s.book_appointment("P1001", "NOPE")["error_code"], "UNKNOWN_SLOT")
        self.assertEqual(self.s.book_appointment("P0000", "NOPE")["error_code"], "UNKNOWN_PATIENT")

    def test_race_fault_makes_first_booking_fail_once(self):
        set_fault(self.w.clinic_db, "race_on_first_booking", 1)
        slot = self.w.first_open_slot("Dermatology")
        first = self.s.book_appointment("P1001", slot["slot_id"])
        self.assertEqual(first["error_code"], "SLOT_UNAVAILABLE")
        self.assertEqual(self.w.slot_status(slot["slot_id"]), "booked")  # taken by "someone else"
        alt = first["alternatives"][0]
        self.assertTrue(self.s.book_appointment("P1001", alt["slot_id"])["ok"])  # fault fired only once


class CancelRescheduleTests(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.s = self.w.service

    def tearDown(self):
        self.w.close()

    def test_cancel_more_than_24h_ahead_is_free_and_frees_the_slot(self):
        res = self.s.cancel_appointment("P1001", "A-1001")  # 12 Nov, > 24h away
        self.assertTrue(res["ok"])
        self.assertFalse(res["late_cancellation"])
        self.assertEqual(self.w.slot_status("D01-20261112-0930"), "open")

    def test_cancel_within_24h_is_flagged_late(self):
        slot = self.s.find_available_slots(department="General Medicine", date_from="2026-11-10",
                                           date_to="2026-11-10", part_of_day="morning")["slots"][0]
        appt = self.s.book_appointment("P1001", slot["slot_id"])["appointment"]["appointment_id"]
        res = self.s.cancel_appointment("P1001", appt)
        self.assertTrue(res["ok"])
        self.assertTrue(res["late_cancellation"])
        self.assertIn("policy_note", res)

    def test_cannot_cancel_or_reschedule_someone_elses_appointment(self):
        res = self.s.cancel_appointment("P1001", "A-1002")  # belongs to P1002
        self.assertEqual(res["error_code"], "APPOINTMENT_NOT_FOUND")
        slot = self.w.first_open_slot("Cardiology")
        self.assertEqual(self.s.reschedule_appointment("P1001", "A-1002", slot["slot_id"])["error_code"],
                         "APPOINTMENT_NOT_FOUND")
        self.assertEqual(self.s.list_patient_appointments("P1001")["appointments"][0]["appointment_id"], "A-1001")

    def test_cancelling_twice_is_an_invalid_state(self):
        self.s.cancel_appointment("P1001", "A-1001")
        self.assertEqual(self.s.cancel_appointment("P1001", "A-1001")["error_code"], "INVALID_STATE")

    def test_reschedule_moves_the_booking_atomically(self):
        new = self.w.first_open_slot("General Medicine", date_from="2026-11-16")
        res = self.s.reschedule_appointment("P1001", "A-1001", new["slot_id"])
        self.assertTrue(res["ok"])
        self.assertEqual(self.w.slot_status("D01-20261112-0930"), "open")
        self.assertEqual(self.w.slot_status(new["slot_id"]), "booked")
        appts = {a["appointment_id"]: a["status"] for a in
                 self.s.list_patient_appointments("P1001")["appointments"]}
        self.assertEqual(appts["A-1001"], "rescheduled")
        self.assertEqual(appts[res["appointment"]["appointment_id"]], "confirmed")

    def test_doctor_leave_cancels_by_clinic_and_allows_priority_rebooking(self):
        outcome = apply_doctor_leave_for_patient(self.w.clinic_db, "P1001")
        self.assertEqual(outcome["appointments_cancelled"], 1)
        appt = self.s.list_patient_appointments("P1001")["appointments"][0]
        self.assertEqual(appt["status"], "cancelled_by_clinic")
        self.assertIn("No fee applies", appt["action_required"])
        self.assertEqual(self.s.cancel_appointment("P1001", "A-1001")["error_code"], "INVALID_STATE")
        new = self.w.first_open_slot("General Medicine", date_from="2026-11-16")
        res = self.s.reschedule_appointment("P1001", "A-1001", new["slot_id"])
        self.assertTrue(res["ok"])

    def test_leave_blocks_all_of_that_doctors_slots_that_day(self):
        apply_doctor_leave(self.w.clinic_db, "D05", "2026-11-18")
        res = self.s.find_available_slots(doctor_id="D05", date_from="2026-11-18", date_to="2026-11-18",
                                          one_per_day=False, limit=10)
        self.assertEqual(res["count"], 0)


if __name__ == "__main__":
    unittest.main()
