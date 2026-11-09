from __future__ import annotations

import operator
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

try:
    from langgraph.pregel import RetryPolicy  # pyright: ignore[reportMissingImports, reportAttributeAccessIssue]
except ImportError:  # pragma: no cover - compatibility fallback for newer LangGraph
    from langgraph.types import RetryPolicy

from backend import database

TRANSIENT = (ModelRateLimitError, ModelTimeoutError)
RETRY = RetryPolicy(
    max_attempts=3,
    initial_interval=10,
    backoff_factor=2,
    jitter=False,
    retry_on=TRANSIENT,
)


class Dispatch(TypedDict):
    claims: list[str]
    reports: Annotated[list[str], operator.add]


class DispatchContext(TypedDict):
    run: dict[str, Any]
    new_graph: Callable[..., Any]
    resuming: set[str]


def list_claims(state: Dispatch, config: RunnableConfig) -> dict[str, Any]:
    """List the queue claims the dispatcher should work next."""
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    run = configurable.get("run") if isinstance(configurable, dict) else None
    if isinstance(run, dict):
        run_id = run.get("run_id")
        if run_id:
            run_record = database.get_run(run_id)
            if run_record and run_record.get("todos"):
                claimed = [item["claim_id"] for item in run_record["todos"]]
                return {"claims": claimed}
    waiting = database.list_waiting_claim_ids(25)
    return {"claims": waiting}


def finish_run(state: Dispatch, config: RunnableConfig) -> dict[str, Any]:
    """Finish the run and return any report lines accumulated."""
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    run = configurable.get("run") if isinstance(configurable, dict) else None
    if isinstance(run, dict):
        run_id = run.get("run_id")
        if run_id:
            database.finish_run(run_id, "Dispatch complete")
    return {"reports": state.get("reports", [])}


def work_claim(state: Dispatch, config: RunnableConfig) -> dict[str, Any]:
    """Work one claim in the current run, catching and recording failures."""
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    claim_id = configurable.get("claim_id") if isinstance(configurable, dict) else None
    if not claim_id:
        return {"reports": []}
    work = configurable.get("work") if isinstance(configurable, dict) else None
    report = ""
    if callable(work):
        try:
            run = configurable.get("run") if isinstance(configurable, dict) else None
            run_id = run.get("run_id") if isinstance(run, dict) else None
            report = str(work(claim_id, run_id))
        except Exception as error:  # pragma: no cover - fallback only
            report = (
                f"{claim_id}  left for the next dispatch: "
                f"{type(error).__name__}: {error}"
            )
    return {"reports": [report] if report else []}


def build_graph(work: Callable[[str, str], str]) -> Any:
    """Build a simple dispatcher graph that fans out over claim ids."""
    builder = StateGraph(Dispatch, context_schema=DispatchContext)
    builder.add_node("list_claims", list_claims)
    builder.add_node("work_claim", work_claim)
    builder.add_node("finish_run", finish_run)
    builder.add_edge(START, "list_claims")
    builder.add_conditional_edges(
        "list_claims",
        lambda state: "work_claim" if state.get("claims") else "finish_run",
        {"work_claim": "work_claim", "finish_run": "finish_run"},
    )
    builder.add_edge("work_claim", "finish_run")
    builder.add_edge("finish_run", END)
    graph = builder.compile()
    object.__setattr__(graph, "work", work)
    return graph


def work_run(
    run: dict[str, Any],
    work: Callable[[str, str], str],
    resuming: set[str] | None = None,
) -> list[str]:
    """Run a dispatch or evaluation through the simple dispatcher graph."""
    graph = build_graph(work)
    claims = list_claims(
        {"claims": [], "reports": []},
        {
            "configurable": {
                "run": run,
                "work": work,
                "resuming": set() if resuming is None else resuming,
            }
        },
    ).get("claims", [])
    if not claims:
        return []

    reports: list[str] = []
    for claim_id in claims:
        result = graph.invoke(
            {"claims": [claim_id], "reports": []},
            config={
                "configurable": {
                    "run": run,
                    "work": work,
                    "claim_id": claim_id,
                    "resuming": set() if resuming is None else resuming,
                }
            },
        )
        reports.extend(result.get("reports", []))
    return list(dict.fromkeys(reports))
