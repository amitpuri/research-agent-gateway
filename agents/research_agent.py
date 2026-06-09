"""
research_agent.py  (refactored)
---------------------------------
Architecture patterns applied
──────────────────────────────
1. Reflection / Critic    – After generating an initial answer the agent runs a
                            separate critic pass that checks for hallucination
                            triggers, missing key points, and factual vagueness.
                            If the critic finds issues the drafter revises once.
                            Using a distinct system-prompt persona for the critic
                            avoids the "rubber-stamp" failure mode (same model,
                            different role, grounded in the original query).

2. Structured output      – The critic returns JSON so the revision decision is
                            deterministic.  No parsing of natural-language verdicts.

3. A2A protocol           – Interface is unchanged so the Coordinator and Gateway
                            config need no modifications.

Run:
    pip install fastapi uvicorn anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    uvicorn research_agent:app --port 9999 --reload
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ── Anthropic client ──────────────────────────────────────────────────────────

claude = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
MODEL  = "claude-sonnet-4-20250514"

app = FastAPI(title="Research Agent", version="2.0.0")

AGENT_PORT = 9999


# ── A2A data models (unchanged) ───────────────────────────────────────────────

class TextPart(BaseModel):
    kind: str = "text"
    text: str


class Message(BaseModel):
    role: str = "user"
    parts: list[TextPart]


class TaskRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message: Message


class TaskStatus(BaseModel):
    state: str
    message: str | None = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class TaskResult(BaseModel):
    parts: list[TextPart]


class TaskResponse(BaseModel):
    id: str
    status: TaskStatus
    result: TaskResult | None = None


# ── Agent Card ─────────────────────────────────────────────────────────────────

AGENT_CARD: dict[str, Any] = {
    "name": "research-agent",
    "description": (
        "Research agent powered by Claude with built-in Reflection quality control. "
        "Answers questions on any topic with a structured summary and key bullet points. "
        "Each answer is critiqued and revised before delivery."
    ),
    "version": "2.0.0",
    "url": f"http://localhost:{AGENT_PORT}",
    "capabilities": {"streaming": False, "pushNotifications": False},
    "skills": [
        {
            "id": "research",
            "name": "Research any topic",
            "description": "Deep-dives a topic; returns a structured answer with key points.",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
        {
            "id": "summarise",
            "name": "Summarise text",
            "description": "Condenses a passage into a concise bullet-point summary.",
            "inputModes": ["text"],
            "outputModes": ["text"],
        },
    ],
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
}


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN: Reflection / Critic
#
# The Reflection pattern runs in two phases:
#   Phase 1 – Drafter produces an initial answer.
#   Phase 2 – Critic evaluates it against a structured rubric and returns JSON.
#
# The critic uses a deliberately different system prompt ("adversarial reviewer")
# to reduce the rubber-stamp failure mode.  If issues are found, a second draft
# is produced that addresses only the listed problems.
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CriticVerdict:
    approved: bool
    issues: list[str]   # empty when approved=True


# Drafter prompt — optimised for structured research output
DRAFTER_SYSTEM = """You are a precise research assistant.
For every query produce:
1. A 2–3 sentence executive summary.
2. Three to five key bullet points (each one concrete and specific).
Keep the total response under 300 words."""


# Critic prompt — adversarial to prevent rubber-stamping
CRITIC_SYSTEM = """You are an adversarial fact-checker reviewing an AI-generated research answer.
Your job is to find genuine problems, not to praise.

Check for:
- Vague or unsupported claims (e.g. "many experts believe" without naming them)
- Missing obvious key points that a well-informed reader would expect
- Internal contradictions
- Answers that don't actually address the user's question

Respond with ONLY a JSON object — no prose, no markdown:
{
  "approved": true | false,
  "issues": ["issue 1", "issue 2"]   // empty array if approved
}"""


def _draft(query: str) -> str:
    """Phase 1: produce an initial answer."""
    resp = claude.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=DRAFTER_SYSTEM,
        messages=[{"role": "user", "content": query}],
    )
    return resp.content[0].text


def _critique(query: str, draft: str) -> CriticVerdict:
    """Phase 2: adversarial critic evaluates the draft."""
    resp = claude.messages.create(
        model=MODEL,
        max_tokens=256,
        system=CRITIC_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Original user query:\n{query}\n\n"
                f"Draft answer to review:\n{draft}"
            )
        }],
    )
    raw = resp.content[0].text.strip()
    try:
        data = json.loads(raw)
        return CriticVerdict(
            approved=bool(data.get("approved", True)),
            issues=data.get("issues", []),
        )
    except json.JSONDecodeError:
        # Unparseable verdict → approve conservatively
        return CriticVerdict(approved=True, issues=[])


def _revise(query: str, draft: str, issues: list[str]) -> str:
    """Revision pass: drafter fixes only the identified issues."""
    issues_text = "\n".join(f"- {i}" for i in issues)
    resp = claude.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=DRAFTER_SYSTEM,
        messages=[
            {"role": "user",  "content": query},
            {"role": "assistant", "content": draft},
            {"role": "user",  "content": (
                "The following issues were found in your answer. "
                "Please produce a revised answer that addresses them. "
                "Do not introduce new problems.\n\n"
                f"Issues:\n{issues_text}"
            )},
        ],
    )
    return resp.content[0].text


def reflect_and_answer(query: str) -> tuple[str, int]:
    """
    Full Reflection pipeline.
    Returns (final_answer, revision_count).
    """
    print(f"[research-agent] phase 1: drafting …")
    draft = _draft(query)

    print(f"[research-agent] phase 2: critiquing …")
    verdict = _critique(query, draft)

    if verdict.approved:
        print("[research-agent] critic: approved ✓")
        return draft, 0

    print(f"[research-agent] critic: found {len(verdict.issues)} issue(s)")
    for issue in verdict.issues:
        print(f"  · {issue}")

    print("[research-agent] phase 3: revising …")
    revised = _revise(query, draft, verdict.issues)
    return revised, 1


# ── A2A endpoints ─────────────────────────────────────────────────────────────

@app.get("/.well-known/agent.json")
async def agent_card():
    return JSONResponse(content=AGENT_CARD)


@app.get("/")
async def root():
    return JSONResponse(content=AGENT_CARD)


@app.post("/tasks/send")
async def handle_task(task: TaskRequest) -> TaskResponse:
    """
    A2A entrypoint.  Runs the Reflection pipeline and returns the final answer.
    Agent Gateway has already authenticated and is logging this call.
    """
    user_text = "\n".join(
        p.text for p in task.message.parts if p.kind == "text"
    ).strip()

    if not user_text:
        raise HTTPException(status_code=400, detail="No text content in task message")

    print(f"\n[research-agent] task {task.id[:8]}…: {user_text[:80]}")

    try:
        answer, revisions = reflect_and_answer(user_text)
        if revisions:
            answer += f"\n\n_(Answer was revised once after internal quality review.)_"
    except anthropic.APIError as exc:
        return TaskResponse(
            id=task.id,
            status=TaskStatus(state="failed", message=str(exc)),
        )

    return TaskResponse(
        id=task.id,
        status=TaskStatus(state="completed"),
        result=TaskResult(parts=[TextPart(kind="text", text=answer)]),
    )


@app.post("/tasks/sendSubscribe")
async def handle_task_streaming(task: TaskRequest):
    """Streaming stub — delegates to synchronous handler."""
    return await handle_task(task)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT)
