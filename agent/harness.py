"""Durable claim graph and queue runner."""

import contextvars
import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from typing import Annotated, Any, TypedDict
from uuid import uuid4

import psycopg
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool
from pydantic import SecretStr

from agent import api
from agent.testing import durable_stub_model, scheduled_stub_miss, stub_queue_model
from agent.tools import CLAIM_TOOLS, ClaimContext
from backend.schemas import RunTodoItemInput

logger = logging.getLogger(__name__)
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
CLAIM_TIMEOUT_SECONDS = float(os.getenv("UNDERCOVER_CLAIM_TIMEOUT_SECONDS", "120"))
MAX_MODEL_CALLS = 30


class ClaimState(TypedDict):
    """State saved by the claim graph for one attempt."""

    messages: Annotated[list[AnyMessage], add_messages]
    model_calls: int
    decision: dict[str, Any] | None
    status: str | None


class TooManyModelCalls(RuntimeError):
    """Raised after a claim graph reaches its persisted model-call limit."""


def claim_agent_node(
    state: ClaimState,
    runtime: Runtime[ClaimContext],
    model_with_tools: Any,
    instructions: str,
) -> dict[str, Any]:
    """Ask the model to review the claim with the tools it needs."""
    if state["model_calls"] >= MAX_MODEL_CALLS:
        raise TooManyModelCalls(f"The claim agent made {MAX_MODEL_CALLS} model calls.")
    reply = model_with_tools.invoke([SystemMessage(instructions), *state["messages"]])
    claim_id = runtime.context["claim_id"]
    for call in reply.tool_calls:
        api.record_progress(
            claim_id,
            "Tool call",
            f"{call['name']}({json.dumps(call['args'], sort_keys=True)})",
        )
    if not reply.tool_calls and reply.text:
        api.record_progress(claim_id, "Agent response", str(reply.text))
    return {"messages": [reply], "model_calls": state["model_calls"] + 1}


def after_agent(state: ClaimState) -> str:
    """Run requested tools, or end when the model has finished."""
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return END


def after_tools(state: ClaimState) -> str:
    """Choose whether the graph should ask again, pause or pay."""
    decision = state["decision"]
    if decision is None:
        return "agent"
    if decision["outcome"] != "approve":
        return END
    if state["status"] == "waiting for sign-off":
        return "wait_for_sign_off"
    if state["status"] == "in progress":
        return "pay"
    return END


def wait_for_sign_off(state: ClaimState, runtime: Runtime[ClaimContext]) -> None:
    """Pause this attempt until the backend records handler sign-off."""
    decision = state["decision"]
    interrupt(
        {
            "claim_id": runtime.context["claim_id"],
            "payout_inr": decision["payout_inr"] if decision else 0,
        }
    )


def pay_claim(state: ClaimState, runtime: Runtime[ClaimContext]) -> dict[str, str]:
    """Pay an approved claim through the guarded backend operation."""
    decision = state["decision"]
    if decision is None:
        raise RuntimeError("The graph has no saved approval to pay")
    response = api.pay_claim(runtime.context["claim_id"], decision["payout_inr"])
    return {"status": response["status"]}


@contextmanager
def checkpointer() -> Iterator[Any]:
    """Open the shared Postgres checkpoint saver, or memory for local tests."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        yield InMemorySaver()
        return
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS undercover")
    pool: ConnectionPool[psycopg.Connection[DictRow]] = ConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=4,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": "-c search_path=undercover",
        },
        open=False,
    )
    pool.open(wait=True)
    try:
        saver = PostgresSaver(pool)
        saver.setup()
        yield saver
    finally:
        pool.close()


def model(claim_id: str | None = None) -> BaseChatModel:
    """Build the configured OpenRouter model or a deterministic test model."""
    if os.getenv("UNDERCOVER_AGENT_TEST_MODE") == "1":
        return durable_stub_model(claim_id or "CLM-1001")
    chat_openai = import_module("langchain_openai")
    name = os.getenv("MODEL_NAME")
    return chat_openai.ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        timeout=CLAIM_TIMEOUT_SECONDS,
        reasoning_effort=None if name else "low",
    )


def _langfuse_callbacks() -> list[Any]:
    """Return Langfuse's handler when both configured keys are available."""
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        return [CallbackHandler()]
    return []


def build_claim_graph(
    llm: BaseChatModel, saver: Any, claim_instructions: str | None = None
) -> Any:
    """Build the claim agent as a persisted LangGraph workflow."""
    instructions = claim_instructions or api.get_instructions()["claim_instructions"]
    model_with_tools = llm.bind_tools(CLAIM_TOOLS)
    tool_node = ToolNode(CLAIM_TOOLS, handle_tool_errors=True)

    def agent_node(state: ClaimState, runtime: Runtime[ClaimContext]) -> dict[str, Any]:
        return claim_agent_node(state, runtime, model_with_tools, instructions)

    builder = StateGraph(ClaimState, context_schema=ClaimContext)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)
    builder.add_node("wait_for_sign_off", wait_for_sign_off)
    builder.add_node("pay", pay_claim)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", after_agent, ["tools", END])
    builder.add_conditional_edges(
        "tools", after_tools, ["agent", "wait_for_sign_off", "pay", END]
    )
    builder.add_edge("wait_for_sign_off", "pay")
    builder.add_edge("pay", END)
    return builder.compile(checkpointer=saver)


def _config(claim_id: str, thread_id: str, trace_name: str) -> dict[str, Any]:
    """Build a claim-scoped checkpoint and tracing configuration."""
    return {
        "configurable": {"thread_id": thread_id},
        "callbacks": _langfuse_callbacks(),
        "metadata": {
            "langfuse_session_id": f"{claim_id}:{thread_id}",
            "langfuse_trace_name": trace_name,
        },
    }


def _invoke(
    graph: Any,
    value: Any,
    claim_id: str,
    thread_id: str,
    trace_name: str,
) -> Any:
    """Invoke the claim graph as a root Langfuse run and save each step."""
    config = _config(claim_id, thread_id, trace_name)
    context = {"claim_id": claim_id}
    current_context = contextvars.Context()
    return current_context.run(
        lambda: graph.invoke(value, config, context=context, durability="sync")
    )


def _thread_id(claim_id: str, record: dict[str, Any]) -> str:
    """Keep a stable thread id for the current persisted attempt."""
    if record["thread_id"]:
        return record["thread_id"]
    return api.save_claim_thread(claim_id, str(uuid4()))


def claim_report(claim_id: str, record: dict[str, Any]) -> str:
    """Build the handler-facing outcome line from the saved claim record."""
    decision = record["decision"]
    if decision is None:
        return f"{claim_id}  no decision"
    if decision["outcome"] != "approve":
        return f"{claim_id}  {decision['outcome']}"
    amount = decision["payout_inr"]
    money = f"₹{amount:,.2f}"
    if record["status"] == "waiting for sign-off":
        return f"{claim_id}  approve  {money}, waiting for sign-off"
    if record["payment"] is None:
        return f"{claim_id}  approve  {money}, not paid yet"
    return f"{claim_id}  approve  {money}"


def work_claim(claim_id: str, graph: Any, thread_id: str | None = None) -> str:
    """Start one new claim attempt and run its graph."""
    record = api.get_agent_claim(claim_id)
    if record["decision"] is not None:
        return carry_on(claim_id, graph)
    if record["status"] != "in progress":
        raise RuntimeError("Claim is not in progress")
    selected_thread = thread_id or _thread_id(claim_id, record)
    api.record_progress(
        claim_id,
        "Review started",
        "Checking the story, policy and supporting documents.",
    )
    _invoke(
        graph,
        {
            "messages": [HumanMessage(content=f"Review claim {claim_id}.")],
            "model_calls": 0,
            "decision": None,
            "status": "in progress",
        },
        claim_id,
        selected_thread,
        "Work claim",
    )
    saved = api.get_agent_claim(claim_id)
    if saved["decision"] is None:
        raise RuntimeError("The claim agent finished without recording a decision")
    return claim_report(claim_id, saved)


def _initial_state(claim_id: str) -> dict[str, Any]:
    """Create the first saved message for a claim without a checkpoint."""
    return {
        "messages": [HumanMessage(content=f"Review claim {claim_id}.")],
        "model_calls": 0,
        "decision": None,
        "status": "in progress",
    }


def _resume_without_checkpoint(
    claim_id: str, record: dict[str, Any]
) -> tuple[bool, Any]:
    """Restart an attempt with no checkpoint or finish its saved decision."""
    decision = record["decision"]
    if decision is None:
        return True, _initial_state(claim_id)
    if decision["outcome"] == "approve":
        api.pay_claim(claim_id, decision["payout_inr"])
    return False, None


def _pay_if_ready(claim_id: str, record: dict[str, Any]) -> None:
    """Finish an approved decision whose payment is still missing."""
    decision = record["decision"]
    if decision is None or decision["outcome"] != "approve":
        return
    if record["payment"] is not None:
        return
    if decision["needs_sign_off"] and record["sign_off_handler_id"] is None:
        return
    api.pay_claim(claim_id, decision["payout_inr"])


def _resume_input(
    claim_id: str, record: dict[str, Any], snapshot: Any
) -> tuple[bool, Any]:
    """Choose the saved graph input needed to resume one claim."""
    replay_payment = bool(
        record["status"] == "decided"
        and record["payment"] is not None
        and "pay" in snapshot.next
    )
    if replay_payment:
        return True, None
    if record["status"] != "in progress":
        return False, None
    if snapshot.created_at is None:
        return _resume_without_checkpoint(claim_id, record)
    if "wait_for_sign_off" in snapshot.next:
        return record["sign_off_handler_id"] is not None, Command(resume=True)
    if snapshot.next:
        return True, None
    _pay_if_ready(claim_id, record)
    return False, None


def carry_on(claim_id: str, graph: Any) -> str:
    """Resume one stopped or signed-off claim from its saved checkpoint."""
    record = api.get_agent_claim(claim_id)
    if record["status"] != "in progress" and record["thread_id"] is None:
        return claim_report(claim_id, record)
    thread_id = _thread_id(claim_id, record)
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    should_invoke, value = _resume_input(claim_id, record, snapshot)
    if not should_invoke:
        return claim_report(claim_id, record)
    try:
        current_context = contextvars.Context()
        current_context.run(
            lambda: graph.invoke(
                value,
                _config(claim_id, thread_id, "Carry on"),
                context={"claim_id": claim_id},
                durability="sync",
            )
        )
    except Exception:
        latest = api.get_agent_claim(claim_id)
        if latest["decision"] is None:
            raise
        logger.exception("Could not carry claim %s to completion", claim_id)
    return claim_report(claim_id, api.get_agent_claim(claim_id))


@tool
def save_run_plan(run_id: str, items: list[RunTodoItemInput]) -> str:
    """Reserve claims and persist their ordered queue to-do list."""
    item_dicts = [item.model_dump() for item in items]
    api.select_run_claims(run_id, [item["claim_id"] for item in item_dicts])
    api.plan_run_todos(run_id, item_dicts)
    return json.dumps({"status": "saved", "items": item_dicts})


def _queue_model(
    candidate_ids: list[str], max_claims: int, run_id: str
) -> BaseChatModel:
    """Create the queue planner model."""
    if os.getenv("UNDERCOVER_AGENT_TEST_MODE") == "1":
        return stub_queue_model(candidate_ids, max_claims, run_id)
    return model()


def _plan_run(
    run_id: str,
    run: dict[str, Any],
    waiting: list[dict[str, Any]],
    llm: BaseChatModel | None = None,
) -> None:
    """Ask the queue agent for its ordered capped selection."""
    if run["run_type"] != "queue" or run["selection_complete"]:
        return
    planner = create_agent(
        llm
        or _queue_model(
            [claim["claim_id"] for claim in waiting], run["max_claims"], run_id
        ),
        tools=[save_run_plan],
        system_prompt=api.get_instructions()["queue_instructions"],
    )
    planner.invoke(
        {
            "messages": [
                HumanMessage(
                    content=json.dumps(
                        {
                            "run_id": run_id,
                            "max_claims": run["max_claims"],
                            "waiting_claims": waiting,
                            "required_count": min(run["max_claims"], len(waiting)),
                        }
                    )
                )
            ]
        }
    )


def _completed_outcome(record: dict[str, Any]) -> str | None:
    """Return a terminal outcome when the required payment work is complete."""
    decision = record["decision"]
    if decision is None:
        return None
    if decision["outcome"] != "approve" or record["payment"] is not None:
        return decision["outcome"]
    if decision["needs_sign_off"] and record["sign_off_handler_id"] is None:
        return decision["outcome"]
    return None


def _finish_todo_if_complete(
    run_id: str, todo: dict[str, Any], record: dict[str, Any]
) -> tuple[str | None, bool] | None:
    """Update a selected task already complete before its next attempt."""
    if record["status"] == "needs handler":
        if todo["status"] != "left for handler":
            api.update_run_todo(run_id, todo["todo_id"], "left for handler")
        return None, True
    outcome = _completed_outcome(record)
    if outcome is None:
        return None
    if todo["status"] != "done":
        api.update_run_todo(run_id, todo["todo_id"], "done")
    return outcome, False


def _begin_todo_attempt(
    run_id: str, todo: dict[str, Any], record: dict[str, Any]
) -> int:
    """Reserve a selected task or count its next attempt."""
    if todo["status"] == "in progress":
        return int(todo["attempt_count"])
    if record["status"] == "waiting":
        return api.retry_run_claim(
            run_id, todo["claim_id"], "Reacquiring a selected claim during recovery."
        )
    api.update_run_todo(run_id, todo["todo_id"], "in progress")
    return int(todo["attempt_count"]) + 1


def _process_todo(
    run_id: str, todo: dict[str, Any], saver: Any, graph: Any | None = None
) -> tuple[str | None, bool]:
    """Run or resume one selected claim, retrying its first failure once."""
    claim_id = todo["claim_id"]
    record = api.get_agent_claim(claim_id)
    completed = _finish_todo_if_complete(run_id, todo, record)
    if completed is not None:
        return completed
    attempt = _begin_todo_attempt(run_id, todo, record)
    while True:
        try:
            current = api.get_agent_claim(claim_id)
            thread_id = _thread_id(claim_id, current)
            claim_graph = graph or build_claim_graph(model(claim_id), saver)
            report = (
                carry_on(claim_id, claim_graph)
                if current["decision"] is not None
                else work_claim(claim_id, claim_graph, thread_id)
            )
            current = api.get_agent_claim(claim_id)
            outcome = _completed_outcome(current)
            if outcome is None:
                raise RuntimeError(report)
            api.update_run_todo(run_id, todo["todo_id"], "done")
            return outcome, False
        except Exception as error:
            failure = api.fail_run_attempt(
                run_id, claim_id, f"{type(error).__name__}: {error}"[:500]
            )
            if failure.get("already_complete"):
                return api.get_agent_claim(claim_id)["decision"]["outcome"], False
            if not failure["retry"]:
                return None, True
            attempt = api.retry_run_claim(
                run_id,
                claim_id,
                f"Retrying after persisted failure {failure['failure_count']}.",
            )
            record = api.get_agent_claim(claim_id)
            logger.info("Retrying %s at attempt %s", claim_id, attempt)


def process_saved_run(
    run_id: str,
    saver: Any,
    graph: Any | None = None,
    llm: BaseChatModel | None = None,
) -> str:
    """Plan and process one queue or named-claim run already stored by backend."""
    run = api.get_run(run_id)
    waiting = [
        claim for claim in api.get_queue_claims() if claim["status"] == "waiting"
    ]
    _plan_run(run_id, run, waiting, llm)
    run = api.get_run(run_id)
    if not run["selection_complete"]:
        raise RuntimeError("The queue agent did not save its plan")
    expected = min(run["max_claims"], len(waiting))
    if run["run_type"] == "queue" and len(run["todos"]) != expected:
        raise RuntimeError("The queue agent did not select the full capped batch")
    totals = {"approve": 0, "decline": 0, "refer": 0}
    handler_count = 0
    for todo in run["todos"]:
        if os.getenv("UNDERCOVER_AGENT_TEST_MODE") == "1" and scheduled_stub_miss(
            todo["claim_id"]
        ):
            api.update_run_todo(run_id, todo["todo_id"], "in progress")
            api.fail_run_attempt(
                run_id,
                todo["claim_id"],
                "Test-only interrupted attempt before reconciliation.",
            )
            continue
        outcome, needs_handler = _process_todo(run_id, todo, saver, graph)
        if outcome is not None:
            totals[outcome] += 1
        handler_count += int(needs_handler)
    for todo in api.get_run(run_id)["todos"]:
        if todo["status"] in {"done", "left for handler"}:
            continue
        outcome, needs_handler = _process_todo(run_id, todo, saver, graph)
        if outcome is not None:
            totals[outcome] += 1
        handler_count += int(needs_handler)
    latest = api.get_run(run_id)
    if any(
        item["status"] not in {"done", "left for handler"} for item in latest["todos"]
    ):
        raise RuntimeError("The run reconciliation found unfinished claims")
    summary = (
        f"Reviewed {len(latest['todos'])} claims: {totals['approve']} approved, "
        f"{totals['decline']} declined, {totals['refer']} referred, "
        f"{handler_count} need handler attention."
    )
    api.complete_run(run_id, summary)
    return summary


def run_queue(
    cap: int,
    llm: BaseChatModel | None = None,
    graph: Any | None = None,
    saver: Any | None = None,
) -> str:
    """Carry on saved claim work, then create and process a capped queue run."""
    active_saver = saver or InMemorySaver()
    latest = api.get_latest_run()
    if latest is not None and latest["status"] == "in progress":
        api.interrupt_run(latest["run_id"])
    reports = []
    for claim in api.get_queue_claims():
        if claim["status"] != "in progress":
            continue
        claim_graph = graph or build_claim_graph(model(claim["claim_id"]), active_saver)
        reports.append(carry_on(claim["claim_id"], claim_graph))
    for report in reports:
        print(report)
    waiting = [
        claim for claim in api.get_queue_claims() if claim["status"] == "waiting"
    ]
    if not waiting:
        return "No claims are waiting"
    run_id = api.create_direct_run(cap)
    return process_saved_run(run_id, active_saver, graph, llm)


def run_claim_direct(claim_id: str, saver: Any) -> str:
    """Create and process a one-claim run from the command line."""
    run_id = api.create_direct_claim_run(claim_id)
    return process_saved_run(run_id, saver)
