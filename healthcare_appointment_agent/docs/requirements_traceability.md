# Requirements Traceability Matrix

Where to find the implementation and the evidence for every mandatory item in the assignment.

| Requirement | Implementation | Automated test(s) | Live evidence (demo transcript) |
|---|---|---|---|
| **One AI agent + LLM**, interprets requests, chooses tools, grounded answers | `clinic_agent/agent.py`, `llm.py` (Anthropic / OpenAI-compatible) | `test_agent.py::ToolLoopAndMemoryTests::test_agent_selects_a_tool_then_answers_from_its_result`, `PlanningAndReplanningTests` | `[PLAN]`, `[TOOL CALL]`, `[TOOL RESULT]` lines in `evidence/demo_transcript.md` |
| **Short-term memory** + follow-up referring to earlier dialogue | `memory.py::ShortTermMemory` | `test_memory.py::ShortTermMemoryTests`, `test_agent.py::...test_short_term_memory_followup_sees_earlier_dialogue` | Session 1, turn 3 "book the second option you listed" |
| **Long-term memory** persisted, retrieved in a NEW session | `memory.py::LongTermMemory` (SQLite), `agent.py::end_session` | `test_memory.py::LongTermMemoryTests::test_facts_persist_in_a_brand_new_instance`, `test_agent.py::...test_long_term_memory_is_loaded_in_a_new_session` | Session 2 prints the loaded `<patient_memory>` block and applies it unprompted |
| **RAG**: >=5 documents, chunk, embed, vector DB, retrieve, ground | `rag.py`, `data/kb/` (10 docs), ChromaDB | `test_rag.py` (ingestion, chunking, relevance for 10 queries, off-topic rejection, Chroma round-trip, neural paraphrases) | `[RAG] query=... -> source#section (score)` lines; `python scripts/query_kb.py "..."` |
| **MCP**: functioning server/tool via Python integration, real invocation + result | `mcp_server.py` (FastMCP, 7 tools), `mcp_client.py` (stdio) | `test_mcp_integration.py` (real subprocess: discovery, call, structured errors, agent end-to-end) | `[TOOL CALL] ... (via MCP)` and `[TOOL RESULT]` lines |
| **Prompt engineering**: role, scope, tool rules, evidence, memory, failure | `prompts.py`, `docs/prompt_justification.md` | `test_agent.py` (prompt contains memory block; behaviours enforced in code) | Whole transcript |
| **Architecture diagram** | `docs/architecture.svg`, `docs/architecture.md` | - | - |
| **Planning and replanning** (title of the assignment) | `update_plan` tool, replan hint in `agent.py` | `PlanningAndReplanningTests::test_conflict_triggers_hint_replan_and_successful_rebooking` | `[REPLAN v2]` after change events #1 (slot taken) and #2 (doctor leave) |
| **Error handling** | LLM retry/backoff, tool exceptions, max-steps, MCP timeout | `FailureHandlingTests` (6 tests) | - |
| **Human oversight for consequential actions** | confirmation gate + audit log (`agent.py`, `memory.py`) | `SecurityAndOversightTests` (7 tests) | `[HUMAN CONFIRMATION]` lines; audit log printed at the end |
| **Synthetic data only** | `clinic_db.py` (seeded), `data/kb/` | `test_clinic_tools.py` | - |
| **Reproducible demonstration** | `scripts/run_demo.py`, fixed clinic clock, seeded RNG, README | all tests are deterministic (no network) | `evidence/demo_transcript.md` |

## Web UI
`clinic_agent/web.py` (Starlette), `clinic_agent/web_session.py` (testable core), `web/` (HTML/CSS/JS). Tests: `tests/test_web_session.py` (14), `tests/ui_logic_test.js`.

## Test inventory (87 tests)
| File | Tests | Covers |
|---|---|---|
| `test_rag.py` | 9 | chunking, ingestion, retrieval relevance, no-evidence, Chroma, neural embedder |
| `test_memory.py` | 12 | short-term trimming, persistence, isolation, clinical-data rejection, forget, audit |
| `test_clinic_tools.py` | 19 | slot search, booking rules, race fault, cancellation, reschedule, doctor leave |
| `test_agent.py` | 21 | tool loop, memory, RAG tool, identity binding, confirmation gate, emergency, injection, replanning, failures |
| `test_mcp_integration.py` | 5 | real MCP server subprocess |
| `test_web_session.py` | 14 | UI event streaming, confirmation dialog bridge (approve / decline / timeout), session lifecycle |

Run: `python -m unittest discover -s tests -v` (or `pytest`). Tests that need `mcp` / `chromadb` / the
neural model skip automatically when those are missing.

## Failure cases covered (negative testing)
out-of-scope medical question · possible emergency · request for another patient's data · prompt-injection text in
tool output · slot taken during booking · clinic cancellation · new patient booking a specialist · double booking
· booking limit · cancelling someone else's appointment · human declines · no confirmation channel · unknown tool
· LLM outage and transient error · runaway tool loop · empty model answer · off-topic RAG query (no evidence) ·
clinical data written to memory.
