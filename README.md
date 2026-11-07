<div align="center">

# CareBridge: Autonomous Healthcare Appointment & Clinical Triage AI Agent

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![MCP Stdio](https://img.shields.io/badge/Protocol-MCP%20(stdio)-6366F1?style=for-the-badge)](https://modelcontextprotocol.io/)
[![Vector RAG](https://img.shields.io/badge/RAG-ChromaDB%20%2B%20MiniLM-10B981?style=for-the-badge)](https://www.trychroma.com/)
[![Long-Term Memory](https://img.shields.io/badge/Memory-SQLite%20%2B%20STM-EC4899?style=for-the-badge&logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Tests](https://img.shields.io/badge/Tests-87%20Passing-22C55E?style=for-the-badge)](tests/)

**An enterprise-grade AI agent connected to an LLM that schedules, cancels, and reschedules simulated outpatient clinic appointments via a real Model Context Protocol (MCP) server, answers policy questions using grounded ChromaDB RAG, persists patient preferences across sessions in SQLite, dynamically replans when clinic conditions change, and enforces fail-closed Human-in-the-Loop (HITL) oversight.**

[Architecture](#-system-architecture) • [Requirement Traceability](#-requirement-traceability-matrix) • [Quick Start](#-quick-start) • [Web Command Centre](#-live-web-command-centre) • [3-Session Demo](#-three-session-automated-demonstration) • [Safety & Guardrails](#-defense-in-depth-safety--guardrails)

</div>

---

## Overview

**CareBridge** (`healthcare-appointment-agent`) is a complete reference implementation of **Scenario 17 (Healthcare Appointment Assistant)** developed for **Agent Based Modeling (AIDE3313)**. Rather than relying on fragile prompt-only rules, CareBridge combines probabilistic LLM reasoning with deterministic Python guardrails, protocol-level tool isolation over **MCP `stdio`**, calibrated **Retrieval-Augmented Generation (RAG)** with similarity cutoffs, and a **Dual Memory** architecture separating rolling conversational context from whitelisted cross-session SQLite storage.

All patient records, provider schedules, and clinic policy documents in this repository are **100% synthetic**.

![CareBridge Architecture](docs/architecture.svg)

---

## Key Engineering Highlights

* **First-Class Planning & Dynamic Replanning (`update_plan`):** Planning is not hidden in free-text chain-of-thought. The agent invokes a structured `update_plan` tool declaring its high-level goal, ordered steps, target tool per step, and rationale. When the environment changes mid-execution and an MCP tool returns `ok=false` (e.g., `SLOT_UNAVAILABLE` or a doctor going on leave), the runtime injects a deterministic replan hint and the agent executes `update_plan(is_replan=true)` to compute a compliant alternative path.
* **Real Model Context Protocol (MCP) over `stdio`:** Scheduling operations (`ClinicService`) are exposed through a standalone MCP server (`clinic_agent/mcp_server.py`) and consumed by an MCP client (`clinic_agent/mcp_client.py`) over standard input/output pipes.
* **Grounded 10-Document RAG with Abstention Cutoffs:** Ingests 10 synthetic clinic policy documents (`data/kb/`), chunks and embeds them using `all-MiniLM-L6-v2` into **ChromaDB**, and enforces an explicit similarity threshold. When a query falls below the evidence threshold, `rag.py` returns zero passages and the agent cleanly abstains (*"I couldn't find that"*) rather than hallucinating policy rules.
* **Dual Memory with Privacy-by-Design Whitelisting:**
  * **Short-Term Memory (`ShortTermMemory`):** Maintains a rolling 12-turn conversational window to resolve intra-session pronouns and ordinals (e.g., *"book the second option"*).
  * **Long-Term Memory (`LongTermMemory`):** Persists user scheduling preferences across separate processes and sessions in **SQLite**. Enforces a strict non-clinical category whitelist, blocks medical/diagnostic text from being stored as preferences, supports user-initiated deletion (*right to forget*), and writes an immutable audit log.
* **Defense-in-Depth Guardrails in Code (`safety.py`):**
  * **Emergency Pre-Screen Bypass:** Acute symptoms (e.g., *"I have chest pain"*) are intercepted deterministically in Python code before any LLM call is made.
  * **Hidden `patient_id` Injection:** The active `patient_id` is stripped from LLM-visible tool schemas and injected directly by the runtime (`agent.py`), preventing cross-patient data access or prompt-injected ID spoofing.
  * **Fail-Closed Human Confirmation:** Every state-changing action (`book`, `cancel`, `reschedule`) requires explicit human approval of fresh details fetched directly from the MCP server.
  * **Tool Output Fencing:** External tool outputs are scanned for instruction-like patterns and fenced before re-entering the LLM context window.
* **Provider-Agnostic LLM Layer & 87 Deterministic Tests:** Supports OpenAI, Google Gemini, Groq, OpenRouter, and local Ollama via `OPENAI_BASE_URL`, plus a scripted deterministic test double that runs **87 automated tests** offline without network or API keys.

---

## Requirement Traceability Matrix

Full mapping to test methods and verification artifacts is maintained in **[`docs/requirements_traceability.md`](docs/requirements_traceability.md)**.

| Required Capability | Primary Implementation Files | Verification & Evidence |
| :--- | :--- | :--- |
| **1. AI Agent + LLM (Visible Tool Selection)** | `clinic_agent/agent.py`, `clinic_agent/llm.py` | Live **Agent mind** timeline in Web UI, JSONL traces in `logs/trace_*.jsonl`, and scripted LLM agent tests in `tests/`. |
| **2. Short-Term Memory (Intra-Session)** | `clinic_agent/memory.py::ShortTermMemory` | 12-turn rolling window resolving multi-turn references (*"book the second option"*) in Session 1 of `scripts/run_demo.py`. |
| **3. Long-Term Memory (Cross-Session SQLite)** | `clinic_agent/memory.py::LongTermMemory` | Preferences saved in Session 1 are recalled unprompted in a brand-new process & MCP server in Session 2. |
| **4. RAG Pipeline (10 Docs, Chunk, Embed, ChromaDB)** | `clinic_agent/rag.py`, `data/kb/` (10 Markdown/text docs) | Grounded policy citations + similarity cutoff abstention verified via `scripts/query_kb.py` and unit tests. |
| **5. MCP Server & Client (`stdio` Transport)** | `clinic_agent/mcp_server.py`, `clinic_agent/mcp_client.py` | Real cross-process MCP tool calls wrapping `ClinicService` (`clinic_db.py`, `clinic_tools.py`). |
| **6. Prompt Engineering & Justification** | `clinic_agent/prompts.py`, `docs/prompt_justification.md` | Structured system prompt paired with architectural justification in `docs/prompt_justification.md`. |
| **7. Planning & Dynamic Replanning** | `update_plan` tool + replan hint injection in `clinic_agent/agent.py` | Verified on **Change Event #1** (`SLOT_UNAVAILABLE`) and **Change Event #2** (Doctor on leave cancellation). |
| **8. Testing, Error Handling & Human Oversight** | `clinic_agent/safety.py`, `tests/` (87 tests + `ui_logic_test.js`) | `python -m unittest discover -s tests -v` (87 tests) + `evidence/demo_transcript.md`. |

---

## System Architecture

```text
+-----------------------------------------------------------------------------------+
|                   USER INTERFACES: Web Command Centre / CLI                       |
|      (Clickable Slot Cards, "Agent Mind" Stream, HITL Confirmation Dialog)        |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                     DETERMINISTIC SAFETY PRE-SCREEN (safety.py)                   |
|        Intercepts acute emergencies ("chest pain") in code -> Bypasses LLM        |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                        PRIMARY AGENT ORCHESTRATOR (agent.py)                      |
|   • Injects hidden patient_id           • Enforces fail-closed HITL gate          |
|   • Manages update_plan(is_replan=...)  • Fences untrusted tool outputs           |
+------------+----------------------------+----------------------------+------------+
             |                            |                            |
             v                            v                            v
+-------------------------+  +-------------------------+  +-------------------------+
|   DUAL MEMORY ENGINE    |  |  10-DOC CHROMADB RAG    |  |  MCP CLIENT (stdio)     |
|      (memory.py)        |  |       (rag.py)          |  |    (mcp_client.py)      |
| • STM: Last 12 turns    |  | • all-MiniLM-L6-v2      |  |         |               |
| • LTM: SQLite whitelist |  | • Similarity cutoff     |  |         v (stdio pipe)  |
| • Non-clinical filter   |  | • Cited policy chunks   |  |  MCP SERVER             |
| • Right-to-forget + log |  | • "I don't know" guard  |  |  (mcp_server.py)        |
+-------------------------+  +-------------------------+  |  • ClinicService DB     |
                                                          +-------------------------+
```

### Architectural Distinction: RAG Knowledge Base vs. Long-Term Memory

| Dimension | RAG Knowledge Base (`rag.py` + ChromaDB) | Long-Term Memory (`memory.py` + SQLite) |
| :--- | :--- | :--- |
| **Purpose** | Answers clinic-wide policy and procedure questions (e.g., late cancellation fees, referral rules, prep instructions). | Remembers individual patient scheduling preferences (e.g., preferred days, morning/afternoon windows, language) across sessions. |
| **Scope** | Global & read-only across all patients (10 synthetic clinic documents in `data/kb/`). | Patient-specific (`patient_id`-scoped), mutable, and erasable upon patient request (*right to forget*). |
| **Storage Engine** | Dense vector embeddings (`all-MiniLM-L6-v2`) indexed in **ChromaDB**. | Relational tables in **SQLite** with an immutable audit log. |
| **Safety / Filtering** | Enforces a minimum similarity score; returns empty below threshold to prevent hallucination. | Enforces a strict whitelist of non-clinical categories and rejects medical/diagnostic strings. |

---

## Quick Start

### 1. Clone & Set Up Environment

```bash
git clone https://github.com/<your-username>/healthcare-appointment-agent.git
cd healthcare-appointment-agent

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate                  # Windows PowerShell: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and add your API key. `LLM_PROVIDER=openai` with `OPENAI_BASE_URL` works out-of-the-box for **OpenAI, Gemini, Groq, OpenRouter, and local Ollama**:

```powershell
# Windows PowerShell
copy .env.example .env
notepad .env
```

```bash
# macOS / Linux
cp .env.example .env
nano .env
```

### 3. Seed Databases & Run the 87-Test Verification Suite

*(Note: The first run downloads the compact `all-MiniLM-L6-v2` embedding model, ~80 MB, once).*

```bash
# Seed the SQLite clinic database and ingest all 10 policy documents into ChromaDB
python scripts/setup_data.py

# Run all 87 deterministic unit & integration tests (no network or live LLM key required)
python -m unittest discover -s tests -v

# Optional: Verify live LLM connectivity before running interactive demos
python scripts/check_llm.py
```

---

## Live Web Command Centre

Launch the interactive Web UI (zero build step required):

```bash
python -m clinic_agent.web
```

Open **`http://127.0.0.1:8000`** in your browser. The Web Command Centre provides:
* **Interactive Chat with Clickable Slot Cards:** Browse real-time provider slots and trigger bookings directly.
* **Live "Agent Mind" Telemetry Stream:** Watch plans (`update_plan`), replans (`is_replan=true`), MCP `stdio` tool calls, ChromaDB RAG similarity scores, SQLite memory writes, and safety guardrails stream in real time.
* **Fail-Closed Human-in-the-Loop Dialog:** Inspect fresh appointment parameters fetched from the MCP server and explicitly **Approve** or **Decline** every booking, rescheduling, or cancellation.
* **Editable Long-Term Memory & Audit Log Inspector:** View and manage persisted patient preferences and audit trails.
* **Knowledge-Base Cutoff Visualizer:** Search the 10-document RAG corpus and inspect chunk similarity scores against the evidence threshold.
* **One-Click Change-Event Simulators & Patient Switcher:** Switch between returning patients (`P1001`) and new patients (`P1003`, restricted from direct specialist booking) or inject live clinic disruptions.

### Suggested 5-Step Live Demo Walkthrough
1. **Initial Planning & MCP Lookup:** Click the *Find a dermatologist* prompt card -> watch the agent formulate its plan, invoke the MCP server, and render interactive slot cards.
2. **Change Event #1 (Slot Taken Mid-Flow):** Click **Slot gets taken**, then attempt to book the slot -> observe `SLOT_UNAVAILABLE`, the amber **Replan** card (`update_plan(is_replan=true)`), and the newly proposed alternatives.
3. **Cross-Session Long-Term Memory:** Click **New session** -> ask for an appointment and watch the agent apply remembered preferences from SQLite without being told again.
4. **Change Event #2 (Doctor Goes on Leave):** Click **Doctor goes on leave**, then ask *"check my appointment"* -> watch the agent detect the clinic-side cancellation and dynamically replan using stored preferences.
5. **Deterministic Emergency Guardrail:** Type *"I have chest pain"* -> observe the emergency pre-screen intercept the message in code (bypassing the LLM) and display immediate emergency instructions.

---

## Three-Session Automated Demonstration

Run the complete, reproducible 3-session demonstration from the terminal:

```bash
python scripts/run_demo.py
```

*(Add `--interactive` to answer the Human-in-the-Loop confirmation prompts yourself).*

| Session | Capabilities Demonstrated |
| :--- | :--- |
| **Session 1** | Saves scheduling preferences to SQLite `LongTermMemory`; answers a clinic policy question via **ChromaDB RAG** with source citation; resolves *"book the second option"* via **Short-Term Memory**; encounters **Change Event #1** (slot taken right before booking) -> triggers **REPLAN** (`update_plan(is_replan=true)`) -> rebooks after **Human Confirmation**. |
| **Session 2** | Starts a **brand-new session** (new process, new MCP server instance, empty STM): automatically recalls and applies **Long-Term Memory** preferences without being told again; handles an out-of-scope policy question where retrieval falls below the similarity cutoff -> responds with grounded abstention (*"I couldn't find that"*). |
| **Session 3** | Encounters **Change Event #2** (doctor goes on leave and clinic cancels the appointment) -> agent detects the change, **REPLANS** with remembered preferences, and rebooks; followed by **5 Guardrail Probes**: (1) medical-advice refusal, (2) cross-patient data isolation, (3) user declining a cancellation at the confirmation prompt, (4) forgetting a stored preference (*right to forget*), and (5) acute **emergency pre-screen** answered deterministically by code. |

### Generated Evidence Artifacts
* **Console Trace & Markdown Transcript:** `evidence/demo_transcript.md`
* **Structured JSONL Telemetry Logs:** `logs/trace_*.jsonl`

### Additional Standalone Utility Commands
```bash
# Test RAG retrieval & similarity cutoff directly (no LLM required)
python scripts/query_kb.py "how much is the late cancellation fee"

# Inject a clinic change event on demand from the CLI
python scripts/inject_change_event.py doctor-leave --patient P1001

# Launch the interactive CLI chat for a specific patient
python -m clinic_agent.cli --patient P1001

# Run the MCP server standalone over stdio
python -m clinic_agent.mcp_server
```

---

## Defense-in-Depth Safety & Honest Limitations

### Deterministic Guarantees (Enforced in Code)
1. **Emergency Pre-Screen Bypass (`safety.py`):** High-risk symptoms bypass the LLM completely so the user receives immediate emergency guidance with zero model latency or hallucination risk.
2. **Runtime `patient_id` Injection (`agent.py`):** The LLM cannot choose or override `patient_id` in tool calls; the Python runtime injects the authenticated session's patient identifier.
3. **Fail-Closed HITL Confirmation:** `book`, `cancel`, and `reschedule` tool executions are gated behind explicit human confirmation of parameters fetched fresh from the MCP server.
4. **Non-Clinical Memory Whitelist:** `LongTermMemory` rejects clinical/diagnostic strings and only persists approved scheduling preference categories.
5. **Prompt-Injection Fencing:** Tool outputs containing instruction-like patterns are flagged and fenced before being returned to the model.

### Known Limitations
* **Short-Term Memory Window:** `ShortTermMemory` retains the last 12 conversational turns; older turns are dropped (durable preferences reside in `LongTermMemory`).
* **Heuristic Clinical Filter:** The clinical-content filter protecting `LongTermMemory` uses a deterministic keyword heuristic rather than a trained clinical NER classifier.
* **Simulated Authentication:** Patient identity (`P1001`, `P1002`, `P1003`) is selected at startup or via the UI switcher for demonstration purposes.
* **Test vs. Production Embedder:** Unit tests use a fast lexical hash embedder for offline determinism, while production retrieval uses the neural `all-MiniLM-L6-v2` model.
* **Probabilistic Phrasing:** Natural-language phrasing and citation formatting are generated by the LLM, whereas all safety boundaries listed above are enforced deterministically in Python code.

---

## Repository Structure

```text
healthcare-appointment-agent/
├── clinic_agent/
│   ├── agent.py                  # Core agent loop, update_plan tool, replan hints & HITL gate
│   ├── llm.py                    # Provider-agnostic LLM client + deterministic scripted test double
│   ├── prompts.py                # System prompt templates & grounding instructions
│   ├── memory.py                 # ShortTermMemory (12-turn buffer) & LongTermMemory (SQLite + audit)
│   ├── rag.py                    # ChromaDB ingestion, MiniLM embeddings & similarity cutoff filter
│   ├── mcp_server.py             # Standalone Model Context Protocol (MCP) stdio server
│   ├── mcp_client.py             # MCP stdio client managing cross-process tool execution
│   ├── clinic_tools.py           # Pure Python ClinicService business rules & scheduling logic
│   ├── clinic_db.py              # SQLite schema & synthetic clinic state management
│   ├── safety.py                 # Emergency pre-screen, clinical filter & prompt-injection fencing
│   ├── tracing.py                # Structured JSONL telemetry & event logger
│   ├── runtime.py                # Session bootstrap & dependency wiring
│   ├── cli.py                    # Interactive command-line interface
│   ├── web.py                    # Zero-build Web Command Centre HTTP server
│   └── config.py                 # Environment & threshold configuration
├── data/
│   └── kb/                       # 10 synthetic clinic policy documents (RAG corpus)
├── web/
│   ├── index.html                # Web Command Centre interface
│   ├── styles.css                # Dark/Light responsive clinical theme
│   └── app.js                    # Real-time Agent Mind timeline, slot cards & HITL modals
├── scripts/
│   ├── setup_data.py             # Seeds SQLite clinic DB & builds ChromaDB vector index
│   ├── check_llm.py              # Verifies LLM provider connectivity
│   ├── run_demo.py               # Automated 3-session end-to-end demonstration
│   ├── query_kb.py               # Standalone RAG retrieval & threshold inspector
│   └── inject_change_event.py    # CLI trigger for clinic change events (e.g., doctor-leave)
├── tests/                        # 87 Python unit/integration tests + ui_logic_test.js
├── docs/
│   ├── architecture.svg          # System architecture diagram
│   ├── prompt_justification.md   # Prompt engineering design & clause justification
│   └── requirements_traceability.md # Full rubric-to-code-and-test traceability matrix
├── evidence/
│   └── demo_transcript.md        # Generated verbatim transcript from scripts/run_demo.py
├── requirements.txt              # Python package dependencies
└── .env.example                  # Template environment configuration
```

---

## Authors & Academic Context

* **Course:** Agent Based Modeling (`AIDE3313`) — Assignment 2 (Scenario 17: Healthcare Appointment Assistant)
* **Institution:** University of Central Punjab (UCP), Faculty of Information Technology & Computer Science
* **Instructor:** Zeshan Hussain Chohan

| Group Member | Registration Number |
| :--- | :--- |
| **Syed Raza Abbas Zaidi** | `L1F23BSAI0011` |
| **Sher Ali Saleem** | `L1F23BSAI0017` |
| **Syed Khesham Ali Raza** | `L1F23BSAI0022` |