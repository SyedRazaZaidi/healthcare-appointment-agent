# Architecture

![architecture](architecture.svg)

## Request flow (one patient message)
```mermaid
flowchart TD
    P[Patient message] --> E{Emergency pre-screen}
    E -- yes --> S[Fixed safety reply - LLM bypassed]
    E -- no --> M[Short-term memory + system prompt + long-term memory block]
    M --> L[LLM chooses: answer or tool call]
    L -- tool call --> R{Tool router}
    R --> K[search_clinic_knowledge -> ChromaDB]
    R --> LT[remember / forget -> SQLite]
    R --> PL[update_plan]
    R --> G{Consequential?}
    G -- no --> C[MCP client]
    G -- yes --> H[Human confirmation gate]
    H -- approved --> C
    H -- declined --> D[USER_DECLINED result]
    C -- JSON-RPC stdio --> SRV[MCP server -> ClinicService -> clinic.db]
    K --> O[Observation]
    LT --> O
    PL --> O
    SRV --> O
    D --> O
    O -- ok=false: replan hint --> L
    O --> L
    L -- final text --> A[Answer + trace + audit]
```

## Why these components are separate
* **RAG knowledge base vs long-term memory.** The knowledge base holds clinic-wide documents (same for everyone, read-only,
  embedded and searched by similarity). Long-term memory holds patient-specific facts told to the assistant (key-value rows
  in SQLite, loaded in full at session start, deletable by the patient). Different data, different access pattern.
* **MCP server vs local tools.** Scheduling is an external system with its own rules and its own process; MCP gives a
  standard discovery/call protocol and process isolation. Retrieval and memory are internal capabilities of the agent.
* **Business rules in `ClinicService`, protocol in `mcp_server.py`.** Rules are unit-testable without MCP, and the agent
  cannot bypass them.
