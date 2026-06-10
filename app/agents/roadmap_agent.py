"""
Roadmap Agent — multi-step planning pipeline.

Memory model:
  - "pref" (permanent, global):  wake time, preferred session length, availability, etc.
  - "fact" (session-scoped):     what to study, deadline, num days for THIS planning task.

Facts from session A never bleed into session B.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.db.models import AgentMessage, AgentSession, User
from app.agents import tools as agent_tools
from app.services import llm_client, memory_service
from app.services.time_utils import parse_positive_float, snap_to_30_min


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------

def _load_history(db: Session, session_id: str, user_id: int, max_messages: int = 20) -> str:
    rows = db.exec(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id, AgentMessage.user_id == user_id)
        .order_by(AgentMessage.id)
        .limit(max_messages)
    ).all()
    if not rows:
        return ""
    lines = []
    for msg in rows:
        role = "User" if msg.role == "user" else "Assistant"
        lines.append(f"{role}: {msg.content}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _build_user_block(
    prompt: str,
    history: str,
    prefs: list[str],
    facts: list[str],
    cal_snapshot: str = "",
) -> str:
    parts = []
    if prefs:
        parts.append(
            "PERMANENT USER PREFERENCES (always apply to every plan):\n"
            + "\n".join(f"- {p}" for p in prefs)
        )
    if cal_snapshot:
        parts.append(f"CURRENT CALENDAR STATE (what is already booked):\n{cal_snapshot}")
    if facts:
        parts.append(
            "SESSION FACTS for this planning task (do NOT ask about these again):\n"
            + "\n".join(f"- {f}" for f in facts)
        )
    if history:
        parts.append(f"CONVERSATION HISTORY (do NOT re-ask questions already answered here):\n{history}")
    parts.append(f"User's latest message: {prompt}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class TriageAgent:
    """Single LLM call that extracts facts/prefs AND classifies intent."""

    def triage(
        self,
        prompt: str,
        prefs: list[str],
        facts: list[str],
        history: str = "",
        cal_snapshot: str = "",
    ) -> dict:
        system = """You are a scheduling assistant. Perform TWO tasks in a single response.

TASK 1 — EXTRACT INFORMATION: Pull out any NEW scheduling-relevant information.
  - "prefs": PERMANENT user preferences that apply to ALL future plans.
    Examples: wake-up time, preferred session length, days unavailable per week, break habits.
    Only extract if NOT already in permanent preferences.
  - "facts": FACTS specific to THIS planning task only (do NOT include permanent prefs here).
    Examples: study subject, deadline, total days needed, total hours for THIS topic.
    Only extract facts NOT already in session facts. Return empty list if nothing new.

TASK 2 — CLASSIFY INTENT: Classify into exactly ONE category:
  - "plan": User wants to schedule AND enough detail exists (subject, duration, timeframe).
  - "clarify": User mentions something schedulable but key details are still missing.
  - "chat": Message is PURELY off-topic with zero connection to planning. Use this rarely.

Return JSON only:
{"prefs": ["new pref 1"], "facts": ["new fact 1"], "intent": "plan|clarify|chat"}"""
        content = llm_client.chat_completion(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": _build_user_block(prompt, history, prefs, facts, cal_snapshot)},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        try:
            parsed = json.loads(content)
            prefs_list = parsed.get("prefs", [])
            facts_list = parsed.get("facts", [])
            if not isinstance(prefs_list, list):
                prefs_list = []
            if not isinstance(facts_list, list):
                facts_list = []
            intent = str(parsed.get("intent", "chat")).strip().lower().strip('"').strip("'")
            if intent not in ("plan", "clarify", "chat"):
                if "clarif" in intent:
                    intent = "clarify"
                elif "plan" in intent or "schedul" in intent:
                    intent = "plan"
                else:
                    intent = "chat"
            return {
                "prefs": [str(p) for p in prefs_list if p],
                "facts": [str(f) for f in facts_list if f],
                "intent": intent,
            }
        except (json.JSONDecodeError, TypeError, AttributeError):
            return {"prefs": [], "facts": [], "intent": "chat"}


class ChatAgent:
    def respond(
        self, prompt: str, prefs: list[str], facts: list[str], history: str = "", cal_snapshot: str = ""
    ) -> str:
        system = """You are a polite scheduling assistant. Keep responses brief and focused on scheduling.
IMPORTANT: Do NOT provide tutorials, lessons, academic explanations, or educational content.
If the user mentions a subject (e.g. math, programming), acknowledge it briefly and offer to help them schedule study time for it.
You help with scheduling tips and time management. Stay on topic.
Remember the earlier conversation context."""
        from app.agent.orchestrator import run_agent
        user_block = _build_user_block(prompt, history, prefs, facts, cal_snapshot)
        return run_agent(user_query=prompt, session_messages=user_block, system_prompt=system)


class ClarifyAgent:
    def ask(
        self, prompt: str, prefs: list[str], facts: list[str], history: str = "", cal_snapshot: str = ""
    ) -> str:
        system = """You are a polite, friendly scheduling assistant. The user wants to plan something but some details are missing.

CRITICAL RULES:
1. Read ALL permanent preferences, session facts, and conversation history CAREFULLY before responding.
2. NEVER re-ask for information the user has already provided — in this message, in the history, or in known facts/prefs.
3. Acknowledge what you already know (e.g. "Great, so you're studying X for Y days...").
4. Only ask for details that are GENUINELY still missing.
5. Ask at most 1-2 short, polite, specific questions.
6. Do NOT give tutorials or educational content.

To build a schedule you typically need: what to study/do, total hours or per-session duration, and timeframe or deadline.
If most details are already known, summarize them and ask only what's missing."""
        return llm_client.chat_completion(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": _build_user_block(prompt, history, prefs, facts, cal_snapshot)},
            ],
            temperature=0.3,
        )


class PlannerAgent:
    def plan(
        self, prompt: str, prefs: list[str], facts: list[str], history: str = "", cal_snapshot: str = ""
    ) -> dict:
        system = """
You are a practical study roadmap planner.
You MUST strictly respect ALL constraints from the conversation:
- Session duration (e.g. "1hr sessions" means each task is 60 minutes)
- Number of days
- Total hours
- Any other user-specified constraints from permanent preferences or session facts

Review conversation history and ALL facts carefully before planning. Do NOT ignore constraints.

Return JSON only:
{
  "goal": "short goal",
  "title": "3-5 word session title (e.g. Redis 7-Day Plan)",
  "days": [
    {"day": 1, "focus": "topic", "tasks": [{"title": "task", "duration_minutes": 60}]}
  ]
}
Keep every task at least 30 minutes. Prefer 1-3 tasks per day.
"""
        content = llm_client.chat_completion(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": _build_user_block(prompt, history, prefs, facts, cal_snapshot)},
            ],
            response_format={"type": "json_object"},
        )
        parsed = json.loads(content)

        if "days" not in parsed or not isinstance(parsed["days"], list):
            raise ValueError("Planner did not return days")
        for day in parsed["days"]:
            if "tasks" not in day or not isinstance(day["tasks"], list):
                raise ValueError("Each day must have a tasks list")

        return parsed


class SchedulerAgent:
    def schedule(self, session: Session, user: User, plan: dict, session_id: str, start_after: str | None = None, slack: float = 0.0) -> list[dict]:
        search_start = datetime.fromisoformat(start_after) if start_after else snap_to_30_min(datetime.now())
        scheduled = []
        for day in plan["days"]:
            date = (search_start.date() + timedelta(days=int(day.get("day", 1)) - 1)).isoformat()
            current_start = f"{date}T08:00"
            for task in day.get("tasks", []):
                minutes = max(30, int(task.get("duration_minutes", 60)))
                minutes = round(minutes / 5) * 5
                minutes = round(minutes * (1.0 + slack) / 5) * 5
                free = agent_tools.find_free_time(session, user, current_start, minutes / 60.0)
                blocks = free.get("allocated", [])
                if not blocks:
                    continue
                block = blocks[0]
                db_block = agent_tools.create_pending_block(
                    session,
                    user,
                    session_id,
                    {
                        "date": block["date"],
                        "start": block["start"],
                        "end": block["end"],
                        "label": task.get("title", "Study task"),
                        "color": "#7c6aff",
                        "repeatDays": [],
                    },
                )
                scheduled.append(db_block)
                current_start = f"{block['date']}T{block['end']}"
        return scheduled


class ConflictAgent:
    def detect(self, session: Session, user: User, scheduled: list[dict]) -> list[dict]:
        dates = sorted({block["date"] for block in scheduled if block.get("date")})
        conflicts = []
        for date in dates:
            conflicts.extend(agent_tools.detect_conflicts(session, user, date))
        return conflicts


class ReviewAgent:
    def review(self, plan: dict, scheduled: list[dict], conflicts: list[str]) -> dict:
        goal = plan.get("goal", "Study plan")
        total_minutes = sum(
            (b.get("end_minutes", 0) or 0) - (b.get("start_minutes", 0) or 0)
            for b in scheduled if b.get("start_minutes") is not None
        )
        if not total_minutes:
            total_minutes = sum(
                (int(b["end"].split(":")[0]) * 60 + int(b["end"].split(":")[1])) -
                (int(b["start"].split(":")[0]) * 60 + int(b["start"].split(":")[1]))
                for b in scheduled
            )
        by_date: dict[str, list[dict]] = {}
        for b in scheduled:
            by_date.setdefault(b["date"], []).append(b)
        days_summary = []
        for date in sorted(by_date):
            day_blocks = sorted(by_date[date], key=lambda x: x["start"])
            total_day = sum(
                (int(b["end"].split(":")[0]) * 60 + int(b["end"].split(":")[1])) -
                (int(b["start"].split(":")[0]) * 60 + int(b["start"].split(":")[1]))
                for b in day_blocks
            )
            days_summary.append({
                "date": date,
                "blocks": [{"start": b["start"], "end": b["end"], "label": b.get("label", "Task")} for b in day_blocks],
                "day_minutes": total_day,
            })
        return {
            "goal": goal,
            "total_hours": round(total_minutes / 60, 1),
            "num_days": len(by_date),
            "days": days_summary,
            "conflicts": conflicts,
            "num_blocks": len(scheduled),
        }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_roadmap_agent(
    session: Session,
    user: User,
    prompt: str,
    start_after: str | None = None,
    slack: float = 0.0,
    existing_session_id: str | None = None,
) -> dict:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Prompt is required")
    slack = parse_positive_float(slack, 0.0) or 0.0

    # ── Resolve session ID ────────────────────────────────────────────────
    history = ""
    cal_snapshot = ""
    is_new_session = False

    if existing_session_id:
        prev = session.get(AgentSession, existing_session_id)
        if prev and prev.user_id == user.id:
            session_id = existing_session_id
            history = _load_history(session, session_id, user.id)
            cal_snapshot = prev.calendar_snapshot or ""
        else:
            session_id = str(int(time.time() * 1000))
            is_new_session = True
    else:
        session_id = str(int(time.time() * 1000))
        is_new_session = True

    # ── Create AgentSession row if needed ────────────────────────────────
    if not session.get(AgentSession, session_id):
        cal_snapshot = memory_service.get_calendar_snapshot(session, user)
        session.add(AgentSession(
            id=session_id,
            user_id=user.id,
            prompt=prompt,
            status="active",
            calendar_snapshot=cal_snapshot,
        ))
        session.commit()

    # ── Update last_accessed_at whenever this session is used ────────────
    _update_last_accessed(session, session_id)

    # ── Load memory — SCOPED correctly ───────────────────────────────────
    # Permanent prefs: global, always included
    prefs = [m.content for m in memory_service.list_prefs(session, user)]
    # Session facts: scoped to THIS session only
    facts = [m.content for m in memory_service.list_session_facts(session, user, session_id)]

    # ── Triage: extract new prefs/facts + classify intent ────────────────
    triage = TriageAgent().triage(prompt, prefs, facts, history, cal_snapshot)
    new_prefs = triage["prefs"]
    new_facts = triage["facts"]
    intent = triage["intent"]

    if new_prefs:
        memory_service.save_prefs(session, user, new_prefs)
        prefs = new_prefs + prefs   # prepend so freshest appear first

    if new_facts:
        memory_service.save_session_facts(session, user, session_id, new_facts)
        facts = new_facts + facts

    # ── Save user message ─────────────────────────────────────────────────
    session.add(AgentMessage(user_id=user.id, session_id=session_id, role="user", content=prompt))
    session.commit()

    # ── Route by intent ───────────────────────────────────────────────────
    if intent == "chat":
        response = ChatAgent().respond(prompt, prefs, facts, history, cal_snapshot)
        session.add(AgentMessage(user_id=user.id, session_id=session_id, role="assistant", content=response))
        _auto_name_session(session, session_id, prompt)
        session.commit()
        return {
            "response": response,
            "scheduled": [],
            "session_id": session_id,
            "plan": None,
            "conflicts": [],
            "intent": "chat",
        }

    if intent == "clarify":
        response = ClarifyAgent().ask(prompt, prefs, facts, history, cal_snapshot)
        session.add(AgentMessage(user_id=user.id, session_id=session_id, role="assistant", content=response))
        _auto_name_session(session, session_id, prompt)
        session.commit()
        return {
            "response": response,
            "scheduled": [],
            "session_id": session_id,
            "plan": None,
            "conflicts": [],
            "intent": "clarify",
        }

    # intent == "plan"
    plan = PlannerAgent().plan(prompt, prefs, facts, history, cal_snapshot)
    scheduled = SchedulerAgent().schedule(session, user, plan, session_id, start_after, slack)
    conflicts = ConflictAgent().detect(session, user, scheduled)
    review = ReviewAgent().review(plan, scheduled, conflicts)

    # Update session title once we have a plan
    agent_session = session.get(AgentSession, session_id)
    if agent_session and not agent_session.title:
        agent_session.title = plan.get("title", plan.get("goal", "Untitled Plan"))[:80]
        session.add(agent_session)

    review_text = _format_review(review)
    session.add(AgentMessage(user_id=user.id, session_id=session_id, role="assistant", content=review_text))
    session.commit()

    return {
        "response": review_text,
        "scheduled": scheduled,
        "session_id": session_id,
        "plan": plan,
        "conflicts": conflicts,
        "intent": "plan",
        "review": review,
    }


def _format_review(review: dict) -> str:
    lines = [f"Draft roadmap: {review['goal']}", ""]
    for day in review["days"]:
        d = datetime.fromisoformat(day["date"])
        day_name = d.strftime("%a %d %b")
        h, m = divmod(day["day_minutes"], 60)
        duration_str = f"{h}h{m:02d}m" if m else f"{h}h"
        lines.append(f"{day_name} ({duration_str})")
        for block in day["blocks"]:
            lines.append(f"  {block['start']}–{block['end']}  {block['label']}")
        lines.append("")
    if review["conflicts"]:
        lines.append(f"Warning: {len(review['conflicts'])} conflict(s) need review.")
        lines.append("")
    lines.append(f"Total: {review['total_hours']}h across {review['num_days']} days, {review['num_blocks']} blocks.")
    lines.append("These blocks are pending. Confirm to save them, or reject to remove them.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _update_last_accessed(session: Session, session_id: str) -> None:
    """Bump last_accessed_at so 'restart time' is always current."""
    agent_session = session.get(AgentSession, session_id)
    if agent_session:
        agent_session.last_accessed_at = datetime.now(timezone.utc)
        session.add(agent_session)
        session.commit()


def _auto_name_session(session: Session, session_id: str, prompt: str) -> None:
    """Set a short title from the user's prompt if the session has no title yet."""
    agent_session = session.get(AgentSession, session_id)
    if agent_session and not agent_session.title:
        words = prompt.strip().split()
        # Take first 6 words, capitalise first, max 60 chars
        short = " ".join(words[:6])
        if len(words) > 6:
            short += "…"
        agent_session.title = short[:60]
        session.add(agent_session)


# ---------------------------------------------------------------------------
# Confirm / Reject
# ---------------------------------------------------------------------------

def confirm_agent_plan(session: Session, user: User, session_id: str) -> dict:
    accepted = agent_tools.commit_pending_plan(session, user, session_id)
    agent_session = session.get(AgentSession, session_id)
    if agent_session and agent_session.user_id == user.id:
        agent_session.status = "confirmed"
        agent_session.finished_at = datetime.now(timezone.utc)
        session.add(agent_session)
        session.commit()
    return {"success": True, "count": len(accepted)}


def reject_agent_plan(session: Session, user: User, session_id: str) -> dict:
    agent_tools.reject_pending_plan(session, user, session_id)
    agent_session = session.get(AgentSession, session_id)
    if agent_session and agent_session.user_id == user.id:
        agent_session.status = "rejected"
        agent_session.finished_at = datetime.now(timezone.utc)
        session.add(agent_session)
        session.commit()
    return {"success": True}
