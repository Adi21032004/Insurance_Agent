from __future__ import annotations

import logging
import operator
from collections.abc import Callable
from typing import Annotated, Any, NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import RetryPolicy, Send

from agent import api, harness

logger = logging.getLogger(__name__)
RETRY = RetryPolicy(
    max_attempts=3,
    initial_interval=10,
    retry_on=harness.TRANSIENT,
)


class Dispatch(TypedDict):
    claims: list[str]
    reports: Annotated[list[str], operator.add]
    claim_id: NotRequired[str]


class DispatchContext(TypedDict):
    run: dict[str, Any]
    work: Callable[[str, str], str]
    resuming: set[str]


def list_claims(state: Dispatch, runtime: Runtime[DispatchContext]) -> dict[str, Any]:
    """Return each selected claim for parallel dispatch."""
    resuming = runtime.context.get("resuming", set())
    return {
        "claims": [claim_id for claim_id in state["claims"] if claim_id not in resuming]
    }


def fan_out(state: Dispatch, runtime: Runtime[DispatchContext]) -> str | list[Send]:
    """Send one independent task to the graph for every selected claim."""
    if not state["claims"]:
        return "finish_run"
    return [
        Send("work_claim", {"claims": [], "reports": [], "claim_id": claim_id})
        for claim_id in state["claims"]
    ]


def work_claim(state: Dispatch, runtime: Runtime[DispatchContext]) -> dict[str, Any]:
    """Work one claim and return its report line."""
    claim_id = state.get("claim_id")
    if claim_id is None:
        raise ValueError("A dispatched claim task must include claim_id")
    run_id = runtime.context["run"]["run_id"]
    try:
        report = runtime.context["work"](claim_id, run_id)
    except harness.TRANSIENT as error:
        execution_info = runtime.execution_info
        if (
            execution_info is not None
            and execution_info.node_attempt < RETRY.max_attempts
        ):
            raise
        logger.exception("Claim %s exhausted transient retries", claim_id)
        report = (
            f"{claim_id}  left for the next dispatch: {type(error).__name__}: {error}"
        )
    except Exception:
        logger.exception("Claim %s failed; retrying once", claim_id)
        try:
            report = runtime.context["work"](claim_id, run_id)
        except Exception as retry_error:
            logger.exception("Claim %s failed its in-dispatch retry", claim_id)
            report = (
                f"{claim_id}  left for the next dispatch: "
                f"{type(retry_error).__name__}: {retry_error}"
            )
    return {"reports": [report]}


def finish_run(state: Dispatch, runtime: Runtime[DispatchContext]) -> dict[str, Any]:
    """Finish the persisted run after every dispatched claim has returned."""
    api.finish_run(runtime.context["run"]["run_id"])
    return {"reports": []}


def build_graph() -> Any:
    """Build the fan-out graph; execution concurrency is set per run."""
    builder = StateGraph(Dispatch, context_schema=DispatchContext)
    builder.add_node("list_claims", list_claims)
    builder.add_node("work_claim", work_claim, retry_policy=RETRY)
    builder.add_node("finish_run", finish_run)
    builder.add_edge(START, "list_claims")
    builder.add_conditional_edges(
        "list_claims",
        fan_out,
        {"work_claim": "work_claim", "finish_run": "finish_run"},
    )
    builder.add_edge("work_claim", "finish_run")
    builder.add_edge("finish_run", END)
    return builder.compile()


def work_run(
    run: dict[str, Any],
    work: Callable[[str, str], str],
    resuming: set[str] | None = None,
) -> list[str]:
    """Fan out a run's claims with no more workers than its agent limit."""
    claims = run.get("claim_ids", [])
    agents = run.get("agents", run.get("max_claims", 1))
    if not isinstance(agents, int) or agents < 1:
        raise ValueError("A dispatch must have at least one claim agent")
    result = build_graph().invoke(
        {"claims": claims, "reports": []},
        config=RunnableConfig(max_concurrency=agents),
        context={
            "run": run,
            "work": work,
            "resuming": set() if resuming is None else resuming,
        },
    )
    return result.get("reports", [])
