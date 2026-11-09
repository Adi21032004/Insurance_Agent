# Code standards

1. Keep it simple; do not overcomplicate.
2. Add comments only when necessary.
3. Be concise: short documentation and no emojis.
4. Handle exceptions only when necessary, and never hide failures behind defaults.
5. Use uv: always `uv run`, never `python3`.
6. TypeScript uses strict mode; never use `any`.
7. Define database tables in the shared SQLAlchemy `MetaData` and use parameterized, cross-database SQLAlchemy Core operations.
8. Keep claim operations in `backend/main.py` and dispatch, evaluation and scoreboard operations in `backend/runs.py`.
9. Keep HTTP service addresses and calls in their designated client modules: `agent/api.py` for agent-to-backend calls and `backend/agent_service.py` for backend-to-agent calls.
10. Build the dispatcher with LangGraph's `StateGraph`, `Send`, `RetryPolicy` and `max_concurrency`; it chooses and coordinates work but does not call a model.
11. Give each claim its own graph and checkpoint thread. The dispatcher reaches claim work only through `harness.work_in_run`.
12. Tests use deterministic stub models and temporary databases. Automated checks must not call a live model or require live credentials.
13. Keep the agent in five parts: `api.py` for backend calls, `tools.py` for claim tools, `harness.py` for claim graphs, `dispatcher.py` for parallel run coordination, and `service.py` for the HTTP service. The command-line entry point handles one claim only.
