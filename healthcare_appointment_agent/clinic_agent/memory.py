"""Two kinds of memory, deliberately kept separate.

* ``ShortTermMemory``  - the running conversation of ONE session (RAM only).
  It is what lets the agent resolve "the second one" or "that day".
* ``LongTermMemory``   - durable facts and history about ONE patient, stored in
  SQLite and reloaded in every NEW session. It is what lets the agent say
  "since you prefer mornings..." without being told again.

Long-term memory is *not* the knowledge base: the RAG store holds clinic-wide
documents that are the same for every patient; long-term memory holds
patient-specific facts that the patient told us.

Privacy by design: long-term memory accepts only a whitelist of non-clinical
categories and rejects values that look like medical information
(data minimisation). Patients can ask for any fact to be forgotten.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Short-term memory
# --------------------------------------------------------------------------


class ShortTermMemory:
    """Session conversation buffer, trimmed by *turns* (never mid-turn).

    A turn starts with a user message and includes every assistant/tool
    message that followed it. Trimming whole turns guarantees we never send
    an orphaned ``tool_result`` (which LLM APIs reject).
    """

    def __init__(self, max_turns: int = 12):
        self.max_turns = max_turns
        self._turns: list[list[dict]] = []
        self.dropped_turns = 0

    def begin_turn(self, user_message: dict) -> None:
        self._turns.append([user_message])
        while len(self._turns) > self.max_turns:
            self._turns.pop(0)
            self.dropped_turns += 1

    def add(self, message: dict) -> None:
        if not self._turns:
            raise RuntimeError("begin_turn() must be called before add()")
        self._turns[-1].append(message)

    @property
    def messages(self) -> list[dict]:
        return [m for turn in self._turns for m in turn]

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    def clear(self) -> None:
        self._turns.clear()


# --------------------------------------------------------------------------
# Long-term memory
# --------------------------------------------------------------------------

ALLOWED_CATEGORIES = (
    "scheduling_preference",   # preferred doctor, time of day, days to avoid
    "accessibility_need",      # wheelchair access, interpreter
    "communication_preference",  # reminder channel, language
    "other_non_clinical",
)

# Heuristic screen for clinical content that must never be stored by the assistant.
_CLINICAL = re.compile(
    r"diagnos|symptom|medicat|prescri|\bmg\b|\bdose|cancer|diabet|\bhiv\b|depress|anxiet|"
    r"pregnan|asthma|hypertens|blood pressure|allerg|infection|surgery|therapy|disease|"
    r"\bpain\b|\brash\b|test result",
    re.I,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_facts (
    patient_id TEXT NOT NULL,
    category   TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    source_session TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (patient_id, key)
);
CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    summary    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    session_id TEXT,
    patient_id TEXT,
    tool       TEXT NOT NULL,
    args_json  TEXT NOT NULL,
    human_confirmed INTEGER,          -- NULL = not a consequential action
    ok         INTEGER,
    summary    TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LongTermMemory:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- facts -------------------------------------------------------
    def remember(self, patient_id: str, category: str, key: str, value: str,
                 session_id: str | None = None) -> dict:
        key = re.sub(r"[^a-z0-9_]+", "_", key.strip().lower()).strip("_")
        value = value.strip()
        if category not in ALLOWED_CATEGORIES:
            return {"ok": False, "error": f"category must be one of {list(ALLOWED_CATEGORIES)}"}
        if not key or not value:
            return {"ok": False, "error": "key and value must be non-empty"}
        if len(value) > 200:
            return {"ok": False, "error": "value too long (max 200 characters)"}
        if _CLINICAL.search(value) or _CLINICAL.search(key):
            return {
                "ok": False,
                "error": "Clinical/medical information is never stored in assistant memory.",
            }
        ts = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO memory_facts(patient_id, category, key, value, source_session, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(patient_id, key) DO UPDATE SET value=excluded.value,"
                " category=excluded.category, source_session=excluded.source_session, updated_at=excluded.updated_at",
                (patient_id, category, key, value, session_id, ts, ts),
            )
        return {"ok": True, "saved": {"category": category, "key": key, "value": value}}

    def forget(self, patient_id: str, key: str) -> dict:
        """Delete one fact, or every fact if ``key == '*'``."""
        with self._conn() as conn:
            if key == "*":
                cur = conn.execute("DELETE FROM memory_facts WHERE patient_id=?", (patient_id,))
                conn.execute("DELETE FROM session_summaries WHERE patient_id=?", (patient_id,))
            else:
                norm = re.sub(r"[^a-z0-9_]+", "_", key.strip().lower()).strip("_")
                cur = conn.execute("DELETE FROM memory_facts WHERE patient_id=? AND key=?", (patient_id, norm))
        return {"ok": True, "deleted": cur.rowcount}

    def facts(self, patient_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT category, key, value, updated_at FROM memory_facts WHERE patient_id=? ORDER BY category, key",
                (patient_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- session summaries ------------------------------------------
    def add_session_summary(self, session_id: str, patient_id: str, summary: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO session_summaries(session_id, patient_id, summary, created_at)"
                " VALUES (?,?,?,?)",
                (session_id, patient_id, summary, _now()),
            )

    def recent_summaries(self, patient_id: str, n: int = 3) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT session_id, summary, created_at FROM session_summaries WHERE patient_id=?"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (patient_id, n),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ---- audit trail (human-oversight evidence) ------------------------
    def log_audit(self, *, session_id: str | None, patient_id: str, tool: str, args: dict[str, Any],
                  human_confirmed: bool | None, ok: bool | None, summary: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO audit_log(ts, session_id, patient_id, tool, args_json, human_confirmed, ok, summary)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (_now(), session_id, patient_id, tool, json.dumps(args, sort_keys=True),
                 None if human_confirmed is None else int(human_confirmed),
                 None if ok is None else int(ok), summary),
            )

    def audit_rows(self, *, session_id: str | None = None, patient_id: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM audit_log WHERE 1=1", []
        if session_id:
            sql += " AND session_id=?"
            params.append(session_id)
        if patient_id:
            sql += " AND patient_id=?"
            params.append(patient_id)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY id", params).fetchall()]

    # ---- prompt injection point -------------------------------------
    def profile_block(self, patient_id: str) -> str:
        """Render stored memory as a block for the system prompt."""
        facts = self.facts(patient_id)
        summaries = self.recent_summaries(patient_id)
        lines = ['<patient_memory trust="saved from earlier sessions; may be outdated">']
        if facts:
            lines.append("Saved preferences:")
            for f in facts:
                lines.append(f"- [{f['category']}] {f['key']}: {f['value']} (saved {f['updated_at'][:10]})")
        else:
            lines.append("No saved preferences yet.")
        if summaries:
            lines.append("Earlier sessions:")
            for s in summaries:
                lines.append(f"- {s['created_at'][:10]}: {s['summary']}")
        lines.append("</patient_memory>")
        return "\n".join(lines)
