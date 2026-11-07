"""Short-term and long-term memory tests."""
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401

from clinic_agent.memory import ALLOWED_CATEGORIES, LongTermMemory, ShortTermMemory


class ShortTermMemoryTests(unittest.TestCase):
    def test_messages_keep_order_within_and_across_turns(self):
        stm = ShortTermMemory()
        stm.begin_turn({"role": "user", "content": "hello"})
        stm.add({"role": "assistant", "content": "hi"})
        stm.begin_turn({"role": "user", "content": "the second one"})
        self.assertEqual([m["content"] for m in stm.messages], ["hello", "hi", "the second one"])

    def test_trimming_drops_whole_turns_and_never_orphans_tool_results(self):
        stm = ShortTermMemory(max_turns=2)
        for i in range(4):
            stm.begin_turn({"role": "user", "content": f"q{i}"})
            stm.add({"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "x", "input": {}}]})
            stm.add({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "r"}]})
            stm.add({"role": "assistant", "content": f"a{i}"})
        msgs = stm.messages
        self.assertEqual(stm.turn_count, 2)
        self.assertEqual(stm.dropped_turns, 2)
        self.assertEqual(msgs[0]["content"], "q2")  # history starts with a plain user message
        uses = {b["id"] for m in msgs if isinstance(m["content"], list) for b in m["content"] if b["type"] == "tool_use"}
        results = {b["tool_use_id"] for m in msgs if isinstance(m["content"], list) for b in m["content"] if b["type"] == "tool_result"}
        self.assertEqual(uses, results)

    def test_add_before_begin_turn_is_an_error(self):
        with self.assertRaises(RuntimeError):
            ShortTermMemory().add({"role": "assistant", "content": "x"})


class LongTermMemoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "mem.db"
        self.ltm = LongTermMemory(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_facts_persist_in_a_brand_new_instance(self):
        self.ltm.remember("P1001", "scheduling_preference", "Preferred Time of Day", "mornings", "s1")
        reopened = LongTermMemory(self.path)  # simulates a new process / new session
        facts = reopened.facts("P1001")
        self.assertEqual(len(facts), 1)
        self.assertEqual((facts[0]["key"], facts[0]["value"]), ("preferred_time_of_day", "mornings"))
        self.assertIn("preferred_time_of_day: mornings", reopened.profile_block("P1001"))

    def test_upsert_updates_instead_of_duplicating(self):
        self.ltm.remember("P1001", "scheduling_preference", "avoid_days", "Mondays")
        self.ltm.remember("P1001", "scheduling_preference", "avoid_days", "Mondays and Fridays")
        facts = self.ltm.facts("P1001")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["value"], "Mondays and Fridays")

    def test_memory_is_isolated_per_patient(self):
        self.ltm.remember("P1001", "scheduling_preference", "doctor", "Dr. Rahman")
        self.assertEqual(self.ltm.facts("P1002"), [])
        self.assertNotIn("Rahman", self.ltm.profile_block("P1002"))

    def test_clinical_information_is_rejected(self):
        for value in ["I have diabetes", "takes 20 mg of something", "symptoms started last week", "my diagnosis is asthma"]:
            res = self.ltm.remember("P1001", "other_non_clinical", "note", value)
            self.assertFalse(res["ok"], value)
        self.assertEqual(self.ltm.facts("P1001"), [])

    def test_unknown_category_and_bad_values_are_rejected(self):
        self.assertFalse(self.ltm.remember("P1001", "medical_history", "k", "v")["ok"])
        self.assertFalse(self.ltm.remember("P1001", "scheduling_preference", "", "v")["ok"])
        self.assertFalse(self.ltm.remember("P1001", "scheduling_preference", "k", "x" * 201)["ok"])
        self.assertIn("scheduling_preference", ALLOWED_CATEGORIES)

    def test_forget_one_and_forget_all(self):
        self.ltm.remember("P1001", "scheduling_preference", "a", "1")
        self.ltm.remember("P1001", "scheduling_preference", "b", "2")
        self.assertEqual(self.ltm.forget("P1001", "a")["deleted"], 1)
        self.assertEqual([f["key"] for f in self.ltm.facts("P1001")], ["b"])
        self.ltm.add_session_summary("s1", "P1001", "Booked something")
        self.ltm.forget("P1001", "*")
        self.assertEqual(self.ltm.facts("P1001"), [])
        self.assertEqual(self.ltm.recent_summaries("P1001"), [])

    def test_session_summaries_are_returned_oldest_first_and_limited(self):
        for i in range(5):
            self.ltm.add_session_summary(f"s{i}", "P1001", f"summary {i}")
        recent = self.ltm.recent_summaries("P1001", n=3)
        self.assertEqual([r["summary"] for r in recent], ["summary 2", "summary 3", "summary 4"])

    def test_audit_log_records_confirmation_state(self):
        self.ltm.log_audit(session_id="s1", patient_id="P1001", tool="book_appointment",
                           args={"slot_id": "X"}, human_confirmed=True, ok=True, summary="Booked")
        self.ltm.log_audit(session_id="s1", patient_id="P1001", tool="cancel_appointment",
                           args={"appointment_id": "A-1"}, human_confirmed=False, ok=False, summary="DECLINED")
        rows = self.ltm.audit_rows(session_id="s1")
        self.assertEqual([r["human_confirmed"] for r in rows], [1, 0])

    def test_empty_profile_block_is_explicit(self):
        self.assertIn("No saved preferences yet", self.ltm.profile_block("P9999"))


if __name__ == "__main__":
    unittest.main()
