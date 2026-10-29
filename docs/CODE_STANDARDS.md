# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.
7. Keep the Durable agent separated into `agent/api.py`, `agent/tools.py`, `agent/harness.py` and `agent/service.py`. The backend calls that service through `backend/agent_service.py`. Define database tables in the shared SQLAlchemy `MetaData` and use parameterized, cross-database SQLAlchemy Core operations.
