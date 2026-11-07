"""Central configuration.

Every tunable value lives here and is read from environment variables (or a
local ``.env`` file). Nothing else in the code base reads ``os.environ``
directly, which keeps the behaviour of the system easy to explain and to
reproduce.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:  # python-dotenv is optional at import time so unit tests run without it
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover
    pass


# Similarity thresholds below which retrieved text is treated as "no evidence".
# Neural embeddings and the offline hash embedder produce different score
# ranges, so each backend has its own calibrated default.
DEFAULT_MIN_SCORE = {
    "chroma-default": 0.25,
    "sentence-transformers": 0.25,
    "hash": 0.12,
}


def _path(env_name: str, default: str) -> Path:
    value = os.environ.get(env_name, default)
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _opt_float(env_name: str) -> float | None:
    raw = os.environ.get(env_name, "").strip()
    return float(raw) if raw else None


@dataclass(frozen=True)
class Settings:
    # --- LLM -----------------------------------------------------------
    llm_provider: str
    llm_model: str
    llm_temperature: float | None
    llm_max_tokens: int
    llm_retries: int
    llm_min_interval: float  # seconds between LLM calls (stay under free-tier rate limits)
    anthropic_api_key: str
    openai_api_key: str
    openai_base_url: str | None
    # --- RAG -----------------------------------------------------------
    kb_dir: Path
    chroma_dir: Path
    collection: str
    embedding_backend: str
    rag_top_k: int
    rag_min_score: float
    # --- Stores ----------------------------------------------------------
    memory_db: Path
    clinic_db: Path
    log_dir: Path
    evidence_dir: Path
    # --- Simulation ----------------------------------------------------
    clinic_name: str
    clinic_now: datetime  # the simulated "current time" of the clinic


def get_settings() -> Settings:
    provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
    default_model = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-4o-mini"}.get(
        provider, "claude-sonnet-5-5"
    )
    backend = os.environ.get("EMBEDDING_BACKEND", "chroma-default").strip().lower()
    min_score = _opt_float("RAG_MIN_SCORE")
    if min_score is None:
        min_score = DEFAULT_MIN_SCORE.get(backend, 0.25)

    return Settings(
        llm_provider=provider,
        llm_model=os.environ.get("LLM_MODEL", default_model),
        llm_temperature=_opt_float("LLM_TEMPERATURE"),
        llm_max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "1500")),
        llm_retries=int(os.environ.get("LLM_RETRIES", "2")),
        llm_min_interval=float(os.environ.get("LLM_MIN_INTERVAL", "0") or 0),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_base_url=os.environ.get("OPENAI_BASE_URL") or None,
        kb_dir=_path("KB_DIR", "data/kb"),
        chroma_dir=_path("CHROMA_DIR", "data/chroma"),
        collection=os.environ.get("RAG_COLLECTION", "clinic_kb"),
        embedding_backend=backend,
        rag_top_k=int(os.environ.get("RAG_TOP_K", "4")),
        rag_min_score=min_score,
        memory_db=_path("MEMORY_DB", "data/agent_memory.db"),
        clinic_db=_path("CLINIC_DB", "data/clinic.db"),
        log_dir=_path("LOG_DIR", "logs"),
        evidence_dir=_path("EVIDENCE_DIR", "evidence"),
        clinic_name=os.environ.get("CLINIC_NAME", "CareBridge Family Clinic"),
        clinic_now=datetime.fromisoformat(os.environ.get("CLINIC_NOW", "2026-11-09T15:00")),
    )
