"""Shared test fixtures.

``World`` builds a complete, isolated, deterministic environment in a temp
directory: seeded clinic DB, long-term memory DB, and a knowledge base that
uses the offline HashEmbedder (so no model download or network is needed).

``FakeToolClient`` is an in-process stand-in for ``MCPToolClient`` that routes
to the REAL ``ClinicService`` (so clinic rules are genuinely exercised). The
real MCP subprocess is covered separately in ``test_mcp_integration.py``.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clinic_agent.agent import ClinicAgent  # noqa: E402
from clinic_agent.clinic_db import connect, get_patient, seed  # noqa: E402
from clinic_agent.clinic_tools import ClinicService  # noqa: E402
from clinic_agent.mcp_client import ToolResult  # noqa: E402
from clinic_agent.memory import LongTermMemory  # noqa: E402
from clinic_agent.rag import HashEmbedder, InMemoryVectorStore, KnowledgeBase  # noqa: E402
from clinic_agent.tracing import Tracer  # noqa: E402

NOW = datetime(2026, 11, 9, 15, 0)
KB_DIR = ROOT / "data" / "kb"


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


_S = {"type": "string"}
FAKE_TOOLS = [
    {"name": "list_doctors", "description": "list doctors", "input_schema": _obj({"department": _S})},
    {"name": "find_available_slots", "description": "find slots", "input_schema": _obj({
        "department": _S, "doctor_id": _S, "date_from": _S, "date_to": _S, "part_of_day": _S,
        "exclude_weekdays": {"type": "array", "items": _S}, "one_per_day": {"type": "boolean"},
        "limit": {"type": "integer"}})},
    {"name": "get_slot", "description": "slot details", "input_schema": _obj({"slot_id": _S}, ["slot_id"])},
    {"name": "list_my_appointments", "description": "my appointments", "input_schema": _obj(
        {"patient_id": _S, "include_past": {"type": "boolean"}}, ["patient_id"])},
    {"name": "book_appointment", "description": "book", "input_schema": _obj(
        {"patient_id": _S, "slot_id": _S, "reason": _S}, ["patient_id", "slot_id"])},
    {"name": "cancel_appointment", "description": "cancel", "input_schema": _obj(
        {"patient_id": _S, "appointment_id": _S}, ["patient_id", "appointment_id"])},
    {"name": "reschedule_appointment", "description": "reschedule", "input_schema": _obj(
        {"patient_id": _S, "appointment_id": _S, "new_slot_id": _S}, ["patient_id", "appointment_id", "new_slot_id"])},
]


class FakeToolClient:
    def __init__(self, service: ClinicService):
        s = service
        self._fns = {
            "list_doctors": s.list_doctors, "find_available_slots": s.find_available_slots,
            "get_slot": s.get_slot, "list_my_appointments": s.list_patient_appointments,
            "book_appointment": s.book_appointment, "cancel_appointment": s.cancel_appointment,
            "reschedule_appointment": s.reschedule_appointment,
        }
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self) -> list[dict]:
        return json.loads(json.dumps(FAKE_TOOLS))

    async def call_tool(self, name: str, arguments: dict) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        data = self._fns[name](**arguments)
        return ToolResult(True, data, json.dumps(data))


async def approve(_summary: str) -> bool:
    return True


async def decline(_summary: str) -> bool:
    return False


class World:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.clinic_db = root / "clinic.db"
        seed(self.clinic_db, NOW)
        self.service = ClinicService(self.clinic_db, NOW)
        self.ltm = LongTermMemory(root / "memory.db")
        self.kb = KnowledgeBase(InMemoryVectorStore(), HashEmbedder(), top_k=4, min_score=0.12)
        self.kb.ingest(KB_DIR)
        self.mcp = FakeToolClient(self.service)

    def close(self) -> None:
        self._tmp.cleanup()

    async def agent(self, llm, patient_id: str = "P1001", confirm=approve, session_id: str | None = None,
                    **kw) -> ClinicAgent:
        sid = session_id or f"t-{id(llm) % 10_000}"
        agent = ClinicAgent(
            llm=llm, mcp=self.mcp, kb=self.kb, ltm=self.ltm, patient=get_patient(self.clinic_db, patient_id),
            clinic_name="CareBridge Family Clinic", clinic_now=NOW, confirm=confirm,
            tracer=Tracer(sid, echo=False), session_id=sid, retry_delay=0.0, **kw,
        )
        await agent.start()
        return agent

    # convenience for assertions
    def slot_status(self, slot_id: str) -> str:
        with connect(self.clinic_db) as conn:
            return conn.execute("SELECT status FROM slots WHERE slot_id=?", (slot_id,)).fetchone()["status"]

    def first_open_slot(self, department: str = "Dermatology", **filters) -> dict:
        return self.service.find_available_slots(department=department, **filters)["slots"][0]
