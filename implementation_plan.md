# Session Auto-Name + Time History Feature

## Changes

### 1. Backend — `roadmap_agent.py`
- For `clarify` and `chat` intents, auto-set a short session title from the user's first prompt (e.g. first 6 words)
- For `plan` intent (already done), keep using the planner's title
- Update `last_accessed_at` every time a session is accessed (resumed or continued)

### 2. Backend — `api/agent.py`
- `/api/agent/sessions` → return `last_accessed_at` and `finished_at` fields too
- `/api/agent/chat` → update `last_accessed_at` on resumed sessions (handled in roadmap_agent)

### 3. Frontend — `index.html`
- Session history item: show `START → FINISH` (or `START → active` if ongoing)
- Auto-naming: frontend shows dynamic title once set
- When resuming a session, show the updated time
