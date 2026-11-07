"""Web UI core: streaming of agent events, the confirmation dialog bridge, session handling."""
import asyncio
import json
import unittest

from helpers import NOW, World

from clinic_agent.agent import ClinicAgent
from clinic_agent.clinic_db import get_patient
from clinic_agent.llm import ScriptedLLM, text_response, tool_response
from clinic_agent.web_session import MAX_MESSAGE_CHARS, WebSession


class WebCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w = World()

    def tearDown(self):
        self.w.close()

    async def session(self, llm, patient_id="P1001", **kw) -> WebSession:
        async def factory(patient, confirm, tracer, sid):
            agent = ClinicAgent(llm=llm, mcp=self.w.mcp, kb=self.w.kb, ltm=self.w.ltm, patient=patient,
                                clinic_name="CareBridge", clinic_now=NOW, confirm=confirm, tracer=tracer,
                                session_id=sid, retry_delay=0.0)
            await agent.start()
            return agent

        s = WebSession(agent_factory=factory, mcp=self.w.mcp, ltm=self.w.ltm,
                       patient=get_patient(self.w.clinic_db, patient_id), **kw)
        await s.start()
        return s

    @staticmethod
    async def until(s: WebSession, *types, timeout=5.0) -> list[dict]:
        items = []
        while True:
            msg = await asyncio.wait_for(s.out.get(), timeout)
            items.append(msg)
            if msg["type"] in types:
                return items


class StreamingTests(WebCase):
    async def test_session_start_sends_session_and_state(self):
        s = await self.session(ScriptedLLM([]))
        first = await self.until(s, "state")
        session_msg = next(m for m in first if m["type"] == "session")
        self.assertEqual(session_msg["patient"]["name"], "Sara Ahmed")
        self.assertEqual(session_msg["memory_loaded"], {"facts": 0, "summaries": 0})
        self.assertEqual([a["appointment_id"] for a in first[-1]["appointments"]], ["A-1001"])

    async def test_turn_streams_plan_tool_and_answer_in_order(self):
        llm = ScriptedLLM([
            tool_response(("update_plan", {"goal": "find", "is_replan": False,
                                           "steps": [{"step": "search", "tool": "find_available_slots", "why": "w"}]})),
            tool_response(("find_available_slots", {"department": "Dermatology", "part_of_day": "morning"})),
            text_response("Here are options."),
        ])
        s = await self.session(llm)
        await self.until(s, "state")
        await s.handle({"type": "user", "text": "dermatology mornings?"})
        msgs = await self.until(s, "turn_end")
        types = [m["type"] for m in msgs]
        self.assertEqual(types[0], "turn_start")
        self.assertEqual(types[-2:], ["state", "turn_end"])
        kinds = [m["event"]["kind"] for m in msgs if m["type"] == "trace"]
        self.assertEqual(kinds, ["plan", "tool_call", "tool_result"])  # user/assistant echoes are filtered
        self.assertLess(types.index("trace"), types.index("assistant"))
        answer = next(m for m in msgs if m["type"] == "assistant")
        self.assertEqual(answer["text"], "Here are options.")
        result = next(m["event"] for m in msgs if m["type"] == "trace" and m["event"]["kind"] == "tool_result")
        self.assertTrue(result["result"]["slots"])
        json.dumps(msgs)  # everything on the queue is plain JSON

    async def test_second_message_while_busy_is_rejected(self):
        slot = self.w.first_open_slot("Dermatology")
        llm = ScriptedLLM([tool_response(("book_appointment", {"slot_id": slot["slot_id"]})), text_response("ok")])
        s = await self.session(llm)
        await self.until(s, "state")
        await s.handle({"type": "user", "text": "book it"})
        await self.until(s, "confirm_request")
        await s.handle({"type": "user", "text": "hello?"})
        err = await self.until(s, "error")
        self.assertIn("still working", err[-1]["message"])
        await s.close()

    async def test_empty_message_is_ignored_and_long_message_is_truncated(self):
        llm = ScriptedLLM([text_response("hi")])
        s = await self.session(llm)
        await self.until(s, "state")
        await s.handle({"type": "user", "text": "   "})
        self.assertTrue(s.out.empty())
        await s.handle({"type": "user", "text": "x" * (MAX_MESSAGE_CHARS + 500)})
        await self.until(s, "turn_end")
        self.assertEqual(len(llm.calls[0]["messages"][-1]["content"]), MAX_MESSAGE_CHARS)


class ConfirmationBridgeTests(WebCase):
    async def test_dialog_approve_executes_the_booking(self):
        slot = self.w.first_open_slot("Dermatology")
        llm = ScriptedLLM([tool_response(("book_appointment", {"slot_id": slot["slot_id"], "reason": "skin"})),
                           text_response("Booked.")])
        s = await self.session(llm)
        await self.until(s, "state")
        await s.handle({"type": "user", "text": "book it"})
        req = (await self.until(s, "confirm_request"))[-1]
        self.assertIn(slot["doctor_name"], req["summary"])
        self.assertEqual(self.w.slot_status(slot["slot_id"]), "open")  # nothing happens before approval
        await s.handle({"type": "confirm", "id": req["id"], "approved": True})
        rest = await self.until(s, "turn_end")
        self.assertEqual(self.w.slot_status(slot["slot_id"]), "booked")
        state = next(m for m in rest if m["type"] == "state")
        self.assertEqual(len(state["appointments"]), 2)
        self.assertTrue(any(a["human_confirmed"] == 1 and a["ok"] == 1 for a in state["audit"]))

    async def test_dialog_decline_changes_nothing(self):
        slot = self.w.first_open_slot("Dermatology")
        llm = ScriptedLLM([tool_response(("book_appointment", {"slot_id": slot["slot_id"]})),
                           text_response("Okay, nothing changed.")])
        s = await self.session(llm)
        await self.until(s, "state")
        await s.handle({"type": "user", "text": "book it"})
        req = (await self.until(s, "confirm_request"))[-1]
        await s.handle({"type": "confirm", "id": req["id"], "approved": False})
        rest = await self.until(s, "turn_end")
        self.assertEqual(self.w.slot_status(slot["slot_id"]), "open")
        confirm_events = [m["event"] for m in rest if m["type"] == "trace" and m["event"]["kind"] == "confirm"]
        self.assertFalse(confirm_events[0]["approved"])

    async def test_no_answer_fails_closed(self):
        slot = self.w.first_open_slot("Dermatology")
        llm = ScriptedLLM([tool_response(("book_appointment", {"slot_id": slot["slot_id"]})), text_response("ok")])
        s = await self.session(llm, confirm_timeout=0.05)
        await self.until(s, "state")
        await s.handle({"type": "user", "text": "book it"})
        msgs = await self.until(s, "turn_end")
        self.assertIn("confirm_timeout", [m["type"] for m in msgs])
        self.assertEqual(self.w.slot_status(slot["slot_id"]), "open")

    async def test_stale_confirmation_ids_are_ignored(self):
        s = await self.session(ScriptedLLM([]))
        self.assertFalse(s.resolve_confirm("nope", True))


class SessionLifecycleTests(WebCase):
    async def test_new_session_saves_summary_and_reloads_memory(self):
        llm = ScriptedLLM([
            tool_response(("remember", {"category": "scheduling_preference", "key": "preferred_time_of_day",
                                        "value": "mornings"})),
            text_response("Noted."),
        ])
        s = await self.session(llm)
        await self.until(s, "state")
        first_id = s.session_id
        await s.handle({"type": "user", "text": "I prefer mornings"})
        await self.until(s, "turn_end")
        await s.handle({"type": "new_session"})
        msgs = await self.until(s, "state")
        types = [m["type"] for m in msgs]
        self.assertIn("session_saved", types)
        session_msg = next(m for m in msgs if m["type"] == "session")
        self.assertNotEqual(session_msg["session_id"], first_id)
        self.assertEqual(session_msg["memory_loaded"]["facts"], 1)
        self.assertEqual(s.agent.stm.turn_count, 0)  # fresh short-term memory

    async def test_forget_message_removes_a_fact_and_refreshes_state(self):
        self.w.ltm.remember("P1001", "scheduling_preference", "days_to_avoid", "Mondays")
        s = await self.session(ScriptedLLM([]))
        await self.until(s, "state")
        await s.handle({"type": "forget", "key": "days_to_avoid"})
        state = (await self.until(s, "state"))[-1]
        self.assertEqual(state["memory"]["facts"], [])

    async def test_close_declines_pending_confirmation_and_saves_summary(self):
        slot = self.w.first_open_slot("Dermatology")
        llm = ScriptedLLM([tool_response(("book_appointment", {"slot_id": slot["slot_id"]})), text_response("x")])
        s = await self.session(llm)
        await self.until(s, "state")
        await s.handle({"type": "user", "text": "book it"})
        await self.until(s, "confirm_request")
        await s.close()  # tab closed while the dialog was open
        self.assertEqual(self.w.slot_status(slot["slot_id"]), "open")
        self.assertTrue(self.w.ltm.recent_summaries("P1001"))

    async def test_llm_failure_is_reported_as_a_normal_assistant_message(self):
        s = await self.session(ScriptedLLM([RuntimeError("down")] * 3))
        await self.until(s, "state")
        await s.handle({"type": "user", "text": "hello"})
        msgs = await self.until(s, "turn_end")
        answer = next(m for m in msgs if m["type"] == "assistant")
        self.assertEqual(answer["guardrail"], "llm_unavailable")


class WebHelperTests(WebCase):
    def test_kb_scored_flags_the_evidence_cutoff(self):
        rows = self.w.kb.scored("how much is the late cancellation fee", k=6)
        self.assertEqual(len(rows), 6)
        self.assertTrue(rows[0]["above"])
        self.assertEqual(rows, sorted(rows, key=lambda r: -r["score"]))
        off = self.w.kb.scored("best recipe for chocolate cake", k=6)
        self.assertTrue(off and not any(r["above"] for r in off))  # shown, but all below the cut-off
        self.assertEqual(self.w.kb.scored("   "), [])

    def test_list_patients(self):
        from clinic_agent.clinic_db import list_patients
        people = list_patients(self.w.clinic_db)
        self.assertEqual([p["patient_id"] for p in people], ["P1001", "P1002", "P1003"])
        self.assertEqual(people[2]["is_new"], 1)


if __name__ == "__main__":
    unittest.main()
