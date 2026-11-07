"""Wires the components together (used by the CLI and the demo script).

    Runtime
      |- clinic DB      (seeded on first use; the simulated world)
      |- KnowledgeBase  (Chroma + embedder; auto-ingests if empty)
      |- LongTermMemory (SQLite; survives across sessions and processes)
      |- MCPToolClient  (spawns the scheduling MCP server)
      `- new_agent()    one fresh ClinicAgent (= one session) per call
"""
from __future__ import annotations

from .agent import ClinicAgent, ConfirmFn
from .clinic_db import get_patient, seed
from .config import Settings
from .llm import LLMClient, build_llm
from .mcp_client import MCPToolClient
from .memory import LongTermMemory
from .rag import ChromaVectorStore, KnowledgeBase, VectorStore, build_embedder
from .tracing import Tracer


def build_knowledge_base(settings: Settings, store: VectorStore | None = None) -> KnowledgeBase:
    embedder = build_embedder(settings.embedding_backend)
    store = store or ChromaVectorStore(settings.chroma_dir, settings.collection)
    kb = KnowledgeBase(store, embedder, top_k=settings.rag_top_k, min_score=settings.rag_min_score)

    # A collection built with a different embedding model has incompatible vectors.
    marker = settings.chroma_dir / "embedder.txt"
    if isinstance(store, ChromaVectorStore):
        if marker.exists() and marker.read_text().strip() != embedder.name:
            store.reset()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(embedder.name)
    kb.ensure_ingested(settings.kb_dir)
    return kb


class Runtime:
    def __init__(self, settings: Settings, llm: LLMClient | None = None):
        self.settings = settings
        self.llm = llm or build_llm(settings)
        self.kb: KnowledgeBase | None = None
        self.ltm: LongTermMemory | None = None
        self.mcp: MCPToolClient | None = None

    async def __aenter__(self) -> "Runtime":
        s = self.settings
        if not s.clinic_db.exists():
            seed(s.clinic_db, s.clinic_now)
        self.kb = build_knowledge_base(s)
        self.ltm = LongTermMemory(s.memory_db)
        self.mcp = MCPToolClient(db_path=s.clinic_db, now_iso=s.clinic_now.isoformat())
        await self.mcp.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        if self.mcp:
            await self.mcp.__aexit__(*exc)

    def login(self, patient_id: str) -> dict:
        """Simulated authentication: look the patient up in the clinic system."""
        patient = get_patient(self.settings.clinic_db, patient_id)
        if patient is None:
            raise SystemExit(f"Unknown patient id '{patient_id}'. Try P1001, P1002 or P1003.")
        return patient

    async def new_agent(self, patient: dict, confirm: ConfirmFn | None, tracer: Tracer | None = None,
                        session_id: str | None = None) -> ClinicAgent:
        s = self.settings
        agent = ClinicAgent(
            llm=self.llm, mcp=self.mcp, kb=self.kb, ltm=self.ltm, patient=patient,
            clinic_name=s.clinic_name, clinic_now=s.clinic_now, confirm=confirm, tracer=tracer,
            session_id=session_id, max_tokens=s.llm_max_tokens, llm_retries=s.llm_retries,
            min_call_interval=s.llm_min_interval,
        )
        await agent.start()
        return agent
