# Agent Gateway — Refactored Architecture

## Patterns applied (from agentpatternscatalog.org)

### 1. Supervisor-Worker
**Where:** `coordinator_agent.py` — `WorkerRegistry` + `discover_worker()`

The coordinator no longer hardcodes which agent handles which task. Instead a `WorkerRegistry` is populated at startup by calling each worker's `/.well-known/agent.json` card. The supervisor picks workers by matching declared skill IDs against what each subtask needs.

**Before:** two `if tool_name == "..."` branches hardcoded to `research-agent`.  
**After:** `registry.find_by_skill(skill_hint)` — works for any number of workers without code changes. Adding a new agent is a config change (gateway YAML + card endpoint), not a code change.

---

### 2. Plan-and-Execute
**Where:** `coordinator_agent.py` — `plan()` + executor loop in `coordinate()`

For multi-step queries the coordinator makes one planning LLM call that returns a structured JSON task list, then dispatches each subtask independently. Simple queries skip the planner entirely and fall straight into the ReAct loop.

**Before:** single-shot routing decision; complex questions were handled as one opaque blob.  
**After:** planner decomposes into typed `SubTask` objects → executor routes each → final synthesis pass consolidates results.

Key benefit: subtasks can target *different* workers (e.g. a research worker for facts + a summarise worker for compression), enabling true parallel specialisation.

---

### 3. ReAct (Reason + Act loop)
**Where:** `coordinator_agent.py` — `react_step()` + `react_loop()`

Single-step queries run as a proper Thought → Action → Observation cycle. Claude sees its own previous tool results before deciding the next action, rather than a one-shot routing pass. The conversation history is threaded explicitly so the model can reason across multiple observations.

**Before:** one `client.messages.create` call, iterate over `content` blocks once.  
**After:** `react_loop` with up to `max_iterations` passes; each pass appends tool results as observations before the next LLM call.

---

### 4. Reflection / Critic
**Where:** `research_agent.py` — `reflect_and_answer()`, `_draft()`, `_critique()`, `_revise()`

The research agent now runs a three-phase pipeline:

```
Phase 1 (Drafter)  →  initial answer
Phase 2 (Critic)   →  structured JSON verdict  { approved, issues[] }
Phase 3 (Reviser)  →  targeted revision if issues were found  (skipped if approved)
```

The critic uses a deliberately adversarial system prompt ("find genuine problems, not praise") to reduce rubber-stamping. The verdict is returned as JSON so the revision decision is deterministic — no prose parsing.

**Before:** single `client.messages.create` → return text.  
**After:** draft → critique → optional revision. Revision count is surfaced in the response so callers know whether quality control triggered.

---

## File map

| File | Role | Patterns |
|---|---|---|
| `coordinator_agent.py` | Orchestrator | Supervisor-Worker, Plan-and-Execute, ReAct |
| `research_agent.py` | Worker | Reflection / Critic |
| `config_minimal.yaml` | Gateway quickstart | (unchanged) |
| `gateway_full.yaml` | Full gateway config | (unchanged) |

---

## Data flow

```
User query
    │
    ▼
coordinator_agent.py
    ├─ WorkerRegistry.discover()       [Supervisor-Worker]
    ├─ plan()  ──JSON subtasks──▶      [Plan-and-Execute]
    │       │
    │       └─ react_loop()            [ReAct]
    │               │
    │               ▼
    │         Agent Gateway :3000
    │         (auth · rate-limit · audit · OTel)
    │               │
    │               ▼
    │         research_agent.py :9999
    │               ├─ _draft()        [Reflection – Phase 1]
    │               ├─ _critique()     [Reflection – Phase 2]
    │               └─ _revise()       [Reflection – Phase 3, if needed]
    │               │
    └─────────────────◀── TaskResponse (A2A)
    │
    ▼
synthesis / final answer → user
```

---

## Running

```bash
# 1. Start the research agent
uvicorn research_agent:app --port 9999

# 2. Start Agent Gateway
agentgateway -f config_minimal.yaml

# 3. Run the coordinator
export ANTHROPIC_API_KEY=sk-ant-...
python coordinator_agent.py
```

---

## Adding a new worker

1. Implement a FastAPI service with `GET /.well-known/agent.json` and `POST /tasks/send`.
2. Add a route in `config_minimal.yaml` pointing to its host/port.
3. No changes to `coordinator_agent.py` — the registry discovers it automatically.
