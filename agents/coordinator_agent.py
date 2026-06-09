"""
coordinator_agent.py  (refactored)
------------------------------------
Architecture patterns applied
──────────────────────────────
1. Supervisor-Worker      – Coordinator maintains a typed WorkerRegistry; workers
                            are discovered at startup and typed by their declared skills.
                            The supervisor never hardcodes which agent handles what.

2. Plan-and-Execute       – For multi-step queries the coordinator first produces a
                            structured plan (list of subtasks), then dispatches each
                            subtask to the best worker.  Single-step queries skip the
                            planning phase and fall straight into ReAct.

3. ReAct (Reason+Act)     – Each execution step runs as: Thought → Tool-call → Observe.
                            Claude reasons about the *current* partial result before
                            deciding the next action, rather than a single routing pass.

4. Structured output      – Plan is returned as JSON so the executor loop is
                            deterministic; no regex scraping of prose.

A2A / Gateway flow (unchanged):
  Coordinator  ──POST /tasks/send──▶  Agent Gateway :3000
                                              │
                                    auth, rate-limit, audit, OTel
                                              │
                                              ▼
                                   Research Agent :9999

Run:
    pip install anthropic httpx
    export ANTHROPIC_API_KEY=sk-ant-...
    python coordinator_agent.py
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import anthropic
import httpx

# ── Constants ─────────────────────────────────────────────────────────────────

GATEWAY_BASE  = "http://localhost:3000"
GATEWAY_ADMIN = "http://localhost:15000"

claude = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
MODEL  = "claude-sonnet-4-20250514"


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN 1 — Supervisor-Worker: Worker Registry
# Workers are discovered via A2A agent-card; the supervisor picks workers by
# matching skill ids, not by hardcoded tool names.
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Worker:
    name: str
    description: str
    route_path: str          # path suffix on the gateway, e.g. "/research"
    skills: list[str]        # skill ids declared in the agent card
    card: dict = field(default_factory=dict)


class WorkerRegistry:
    """
    Supervisor-Worker pattern: the registry is the single source of truth about
    what workers exist and what they can do.  Workers register themselves at
    runtime (via discovery) so the supervisor never has static knowledge of
    their capabilities.
    """

    def __init__(self) -> None:
        self._workers: list[Worker] = []

    def register(self, worker: Worker) -> None:
        self._workers.append(worker)
        print(f"[registry] registered worker '{worker.name}' "
              f"| skills: {worker.skills} | route: {worker.route_path}")

    def find_by_skill(self, skill_hint: str) -> Worker | None:
        """Return the first worker whose skill list contains a matching keyword."""
        hint = skill_hint.lower()
        for w in self._workers:
            if any(hint in s.lower() for s in w.skills):
                return w
        return self._workers[0] if self._workers else None  # fallback

    def as_tool_descriptions(self) -> str:
        """Render workers as a plain-text list for inclusion in LLM prompts."""
        lines = []
        for w in self._workers:
            lines.append(f"- {w.name}: {w.description}  [skills: {', '.join(w.skills)}]")
        return "\n".join(lines) or "(no workers registered)"


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN 2 — Plan-and-Execute: Planner
# The planner calls Claude once to decompose the user query into subtasks.
# Each subtask specifies the required_skill so the executor can route it.
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SubTask:
    id: str
    description: str
    required_skill: str   # matched against WorkerRegistry


def plan(user_query: str, registry: WorkerRegistry) -> list[SubTask]:
    """
    Plan-and-Execute pattern: separate the *what* (planning) from the *how*
    (execution).  Returns an empty list when no planning is needed (simple queries
    are handled directly by the ReAct loop instead).
    """
    worker_list = registry.as_tool_descriptions()

    print("\n[planner] decomposing query …")

    resp = claude.messages.create(
        model=MODEL,
        max_tokens=512,
        system="""You are a task planner. Given a user query and a list of available workers,
decide if the query requires multiple steps.

If yes: return a JSON array of subtasks. Each subtask:
  { "id": "t1", "description": "...", "required_skill": "<skill_id>" }

If the query is simple and can be answered in one step, return an empty array: []

IMPORTANT: Return ONLY valid JSON. No markdown, no prose.""",
        messages=[{
            "role": "user",
            "content": (
                f"Available workers:\n{worker_list}\n\n"
                f"User query:\n{user_query}"
            )
        }],
    )

    raw = resp.content[0].text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Malformed → treat as single-step
        return []

    tasks = [
        SubTask(id=t["id"], description=t["description"],
                required_skill=t.get("required_skill", "research"))
        for t in data
    ]
    if tasks:
        print(f"[planner] plan has {len(tasks)} subtask(s):")
        for t in tasks:
            print(f"  [{t.id}] {t.description}  (skill: {t.required_skill})")
    else:
        print("[planner] single-step query — skipping plan phase")
    return tasks


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN 3 — ReAct: Reason → Act → Observe loop
# Each call to `react_step` is one pass of the loop.
# ══════════════════════════════════════════════════════════════════════════════

# Tool definitions are generated dynamically from the registry so they always
# reflect what workers are actually available.

def build_tools(registry: WorkerRegistry) -> list[dict]:
    tools = []
    for w in registry._workers:
        tools.append({
            "name": f"delegate_to_{w.name.replace('-', '_')}",
            "description": (
                f"{w.description}  "
                f"Use this when the task requires: {', '.join(w.skills)}."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The precise question or task to send to this worker."
                    }
                },
                "required": ["query"],
            },
        })

    tools.append({
        "name": "answer_directly",
        "description": (
            "Answer the user directly without calling any worker. "
            "Use for greetings, clarifications, or questions that need no research."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "response": {"type": "string", "description": "Direct answer text."}
            },
            "required": ["response"],
        },
    })
    return tools


async def react_step(
    query: str,
    registry: WorkerRegistry,
    http: httpx.AsyncClient,
    conversation: list[dict],
) -> tuple[str | None, list[dict]]:
    """
    One iteration of the ReAct loop.

    Returns (final_answer, updated_conversation).
    final_answer is None when the loop should continue (tool observation was
    appended and the caller should loop again).
    """
    tools = build_tools(registry)
    worker_list = registry.as_tool_descriptions()

    # Append current query as new user turn (first iteration) or just call
    if not conversation:
        conversation.append({"role": "user", "content": query})

    resp = claude.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=(
            "You are a supervisor agent.  Think step-by-step (Thought), "
            "choose a tool (Action), then I will give you the result (Observation). "
            "Keep reasoning until you have a final answer.\n\n"
            f"Available workers:\n{worker_list}"
        ),
        messages=conversation,
        tools=tools,
    )

    # Collect assistant turn
    assistant_content = resp.content
    conversation.append({"role": "assistant", "content": assistant_content})

    # Process each block
    tool_results: list[dict] = []
    final_text: str | None = None

    for block in assistant_content:
        if block.type == "text":
            # Claude gave a direct answer — loop is done
            final_text = block.text

        elif block.type == "tool_use":
            tool_name  = block.name
            tool_input = block.input
            tool_id    = block.id

            print(f"\n[react] Thought → Action: {tool_name}")
            print(f"[react] input: {json.dumps(tool_input, indent=2)}")

            if tool_name == "answer_directly":
                observation = tool_input["response"]
                final_text  = observation
            else:
                # Derive which worker to call from the tool name
                # Pattern: delegate_to_<worker_name_with_underscores>
                worker_slug = tool_name.replace("delegate_to_", "").replace("_", "-")
                worker = next(
                    (w for w in registry._workers if w.name == worker_slug),
                    registry.find_by_skill("research"),
                )
                observation = await send_task(http, tool_input["query"], worker)

            print(f"[react] Observation: {observation[:120]}…")

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": observation,
            })

    # Append tool results as a user turn so the loop can continue
    if tool_results:
        conversation.append({"role": "user", "content": tool_results})

    # Stop condition: if stop_reason is "end_turn" or we have a direct answer
    if resp.stop_reason == "end_turn" or final_text is not None:
        # If we stopped but only produced tool calls, run one more pass to get
        # Claude's synthesis of the observations.
        if final_text is None and tool_results:
            return None, conversation   # caller will loop once more
        return final_text, conversation

    return None, conversation   # tool was called; loop continues


async def react_loop(
    query: str,
    registry: WorkerRegistry,
    http: httpx.AsyncClient,
    max_iterations: int = 5,
) -> str:
    """Run the ReAct loop until a final answer is produced."""
    conversation: list[dict] = []
    for i in range(max_iterations):
        print(f"\n[react] iteration {i+1}/{max_iterations}")
        answer, conversation = await react_step(query, registry, http, conversation)
        if answer is not None:
            return answer
    return "[max iterations reached without final answer]"


# ══════════════════════════════════════════════════════════════════════════════
# A2A Gateway helpers (unchanged from original, kept minimal)
# ══════════════════════════════════════════════════════════════════════════════

def a2a_payload(text: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "message": {"role": "user", "parts": [{"kind": "text", "text": text}]},
    }


async def send_task(http: httpx.AsyncClient, task_text: str, worker: Worker) -> str:
    """POST an A2A task through Agent Gateway to the given worker."""
    url = f"{GATEWAY_BASE}{worker.route_path}/tasks/send"
    payload = a2a_payload(task_text)

    print(f"[gateway] → POST {url}  task_id={payload['id']}")

    try:
        resp = await http.post(
            url, json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return f"[Gateway error: {exc}]"

    data  = resp.json()
    state = data.get("status", {}).get("state", "unknown")
    print(f"[gateway] ← status={state}")

    if state != "completed":
        return f"[Task failed: {data.get('status', {}).get('message', 'unknown')}]"

    parts = data.get("result", {}).get("parts", [])
    return "\n".join(p["text"] for p in parts if p.get("kind") == "text")


async def discover_worker(http: httpx.AsyncClient, route_path: str) -> Worker | None:
    """
    Supervisor-Worker pattern: workers declare themselves via an agent card.
    The supervisor discovers capabilities at startup rather than hardcoding them.
    """
    url = f"{GATEWAY_BASE}{route_path}/.well-known/agent.json"
    try:
        resp = await http.get(url, timeout=5)
        resp.raise_for_status()
        card = resp.json()
        return Worker(
            name=card["name"],
            description=card.get("description", ""),
            route_path=route_path,
            skills=[s["id"] for s in card.get("skills", [])],
            card=card,
        )
    except Exception as exc:
        print(f"[discovery] could not reach {url}: {exc}")
        # Fallback: synthesize a worker so the system degrades gracefully
        return Worker(
            name="research-agent",
            description="Research agent (offline discovery – using defaults)",
            route_path=route_path,
            skills=["research", "summarise"],
        )


# ══════════════════════════════════════════════════════════════════════════════
# Main coordinator
# ══════════════════════════════════════════════════════════════════════════════

async def coordinate(user_query: str) -> str:
    print(f"\n{'='*60}")
    print(f"[coordinator] query: {user_query}")
    print(f"{'='*60}")

    async with httpx.AsyncClient() as http:

        # ── Step 1: Supervisor discovers workers at runtime ────────────────
        registry = WorkerRegistry()
        worker = await discover_worker(http, route_path="")
        if worker:
            registry.register(worker)

        # ── Step 2: Planner decides if this is multi-step ─────────────────
        subtasks = plan(user_query, registry)

        if subtasks:
            # ── PATTERN 2: Plan-and-Execute path ──────────────────────────
            results: list[str] = []
            for subtask in subtasks:
                target = registry.find_by_skill(subtask.required_skill)
                print(f"\n[executor] running subtask [{subtask.id}] → worker: {target.name}")
                result = await send_task(http, subtask.description, target)
                results.append(f"[{subtask.id}] {result}")

            # Final synthesis over all subtask results
            synthesis_prompt = (
                f"Original query: {user_query}\n\n"
                "Subtask results:\n" + "\n\n".join(results) + "\n\n"
                "Synthesise these into a single coherent answer for the user."
            )
            synth = claude.messages.create(
                model=MODEL, max_tokens=1024,
                messages=[{"role": "user", "content": synthesis_prompt}],
            )
            return synth.content[0].text

        else:
            # ── PATTERN 3: ReAct path for single-step queries ─────────────
            return await react_loop(user_query, registry, http)


# ── CLI demo ──────────────────────────────────────────────────────────────────

async def main():
    queries = [
        # ReAct path (single step)
        "What is the Agent-to-Agent (A2A) protocol and why does it matter?",
        # Plan-and-Execute path (multi-step)
        "Compare the A2A protocol with MCP, then summarise the key benefits "
        "of a unified AI gateway that supports both.",
        # Direct answer path
        "Hello! What can you help me with?",
    ]

    for q in queries:
        answer = await coordinate(q)
        print(f"\n[coordinator] FINAL ANSWER:\n{answer}\n{'─'*60}")
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
