"""End-to-end tests through a REAL MCP server subprocess (JSON-RPC over stdio).

These prove the MCP requirement: the server is launched as a separate process,
tools are discovered with tools/list and executed with tools/call. Skipped
automatically if the `mcp` package is not installed.
"""
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from helpers import KB_DIR, NOW

from clinic_agent.agent import ClinicAgent
from clinic_agent.clinic_db import get_patient, seed
from clinic_agent.llm import ScriptedLLM, text_response, tool_response
from clinic_agent.memory import LongTermMemory
from clinic_agent.rag import HashEmbedder, InMemoryVectorStore, KnowledgeBase
from clinic_agent.tracing import Tracer

try:
    import mcp  # noqa: F401
    from clinic_agent.mcp_client import MCPToolClient
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

EXPECTED_TOOLS = {"list_doctors", "find_available_slots", "get_slot", "list_my_appointments",
                  "book_appointment", "cancel_appointment", "reschedule_appointment"}


@unittest.skipUnless(HAS_MCP, "mcp package not installed")
class RealMCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "clinic.db"
        seed(self.db, NOW)

    def tearDown(self):
        self._tmp.cleanup()

    def client(self) -> "MCPToolClient":
        return MCPToolClient(db_path=self.db, now_iso=NOW.isoformat())

    async def test_tools_are_discovered_with_schemas(self):
        async with self.client() as c:
            tools = await c.list_tools()
        self.assertEqual({t["name"] for t in tools}, EXPECTED_TOOLS)
        book = next(t for t in tools if t["name"] == "book_appointment")
        self.assertIn("slot_id", book["input_schema"]["properties"])
        self.assertTrue(book["description"])

    async def test_real_tool_invocation_returns_parsed_json(self):
        async with self.client() as c:
            res = await c.call_tool("find_available_slots", {"department": "Dermatology", "part_of_day": "morning",
                                                             "exclude_weekdays": ["Mon"], "limit": 3})
        self.assertTrue(res.ok)
        self.assertTrue(res.data["ok"])
        self.assertLessEqual(res.data["count"], 3)
        print("\n[MCP evidence] find_available_slots ->", res.text[:200])

    async def test_business_rule_violation_crosses_the_protocol_as_structured_error(self):
        async with self.client() as c:
            res = await c.call_tool("cancel_appointment", {"patient_id": "P1001", "appointment_id": "A-1002"})
        self.assertEqual(res.data["error_code"], "APPOINTMENT_NOT_FOUND")

    async def test_unknown_tool_does_not_crash_the_client(self):
        async with self.client() as c:
            res = await c.call_tool("does_not_exist", {})
            again = await c.call_tool("list_doctors", {})  # session still usable
        self.assertFalse(res.ok)
        self.assertTrue(again.data["ok"])

    async def test_agent_books_through_the_real_server_with_human_confirmation(self):
        async with self.client() as c:
            slot = (await c.call_tool("find_available_slots", {"department": "Dermatology", "limit": 1})).data["slots"][0]
            llm = ScriptedLLM([tool_response(("book_appointment", {"slot_id": slot["slot_id"], "reason": "skin check"})),
                               text_response("Booked.")])
            kb = KnowledgeBase(InMemoryVectorStore(), HashEmbedder(), min_score=0.12)
            kb.ingest(KB_DIR)
            approvals = []

            async def approve(summary):
                approvals.append(summary)
                return True

            agent = ClinicAgent(llm=llm, mcp=c, kb=kb, ltm=LongTermMemory(self.root / "m.db"),
                                patient=get_patient(self.db, "P1001"), clinic_name="CareBridge",
                                clinic_now=NOW, confirm=approve, tracer=Tracer("mcp-e2e", echo=False))
            await agent.start()
            reply = await agent.respond("Book that dermatology slot")
            check = (await c.call_tool("get_slot", {"slot_id": slot["slot_id"]})).data
        self.assertEqual(reply.text, "Booked.")
        self.assertEqual(check["slot"]["status"], "booked")
        self.assertIn(slot["doctor_name"], approvals[0])
        self.assertNotIn("patient_id", [p for t in agent._llm_tools if t["name"] == "book_appointment"
                                        for p in t["input_schema"]["properties"]])


if __name__ == "__main__":
    unittest.main()
