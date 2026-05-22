"""
SeeWeeS System — LangGraph implementation.

Graph topology (with Human-in-the-loop checkpoint):

  DataAgent → PlannerAgent → AuditAgent ─┬─(pass/escalate)──→ ReportAgent → HumanCheckpoint → END
                                          └─(retry, ≤3×) ───→ PlannerAgent

HumanCheckpoint is a transparent pass-through unless risk ≥ 65, in which case
it calls LangGraph interrupt() and waits for a manager decision before the
final status is committed.
"""

import uuid
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from core.state import SeeWeeSState, create_state, log_event
from agents.data_agent import data_agent
from agents.planner_agent import planner_agent
from agents.audit_agent import audit_agent, audit_decision
from agents.report_agent import report_agent
from agents.checkpoint_agent import human_checkpoint_agent
from simulator.data_generator import (
    generate_deliveries,
    generate_inventory,
    generate_drivers,
    generate_weather,
    generate_scenario,
)

# ── Module-level MemorySaver (persists state between run and resume) ───────────
_memory = MemorySaver()
_graph_with_memory: object | None = None


def build_graph(checkpointer=None):
    """
    Compile the LangGraph StateGraph.

    Args:
        checkpointer: optional LangGraph checkpointer (MemorySaver) for HITL support.
                      When None the graph runs statelessly (used for what-if / CLI).
    """
    g = StateGraph(SeeWeeSState)

    g.add_node("DataAgent", data_agent)
    g.add_node("PlannerAgent", planner_agent)
    g.add_node("AuditAgent", audit_agent)
    g.add_node("ReportAgent", report_agent)
    g.add_node("HumanCheckpoint", human_checkpoint_agent)

    g.set_entry_point("DataAgent")
    g.add_edge("DataAgent", "PlannerAgent")
    g.add_edge("PlannerAgent", "AuditAgent")
    g.add_conditional_edges(
        "AuditAgent",
        audit_decision,
        {
            "PlannerAgent": "PlannerAgent",
            "ReportAgent": "ReportAgent",
        },
    )
    g.add_edge("ReportAgent", "HumanCheckpoint")
    g.add_edge("HumanCheckpoint", END)

    return g.compile(checkpointer=checkpointer)


def _get_memory_graph():
    """Return the singleton graph compiled with MemorySaver (for HITL runs)."""
    global _graph_with_memory
    if _graph_with_memory is None:
        _graph_with_memory = build_graph(checkpointer=_memory)
    return _graph_with_memory


def describe_graph() -> dict:
    """Return a JSON-serialisable graph description for the /api/graph endpoint."""
    raw = build_graph().get_graph()
    nodes = [n for n in raw.nodes if n not in ("__start__", "__end__")]
    edges = []
    for e in raw.edges:
        if e.source in ("__start__",) or e.target in ("__end__",):
            continue
        edges.append({
            "from": e.source,
            "to": e.target,
            "type": "conditional" if e.conditional else "direct",
        })
    return {
        "name": "SeeWeeS Medical Logistics",
        "entryPoint": "DataAgent",
        "nodes": nodes,
        "edges": edges,
    }


def _make_initial_state(
    scenario: str | dict,
    delivery_count: int,
    driver_count: int,
) -> SeeWeeSState:
    """Build and populate a fresh state dict ready to feed into the graph."""
    state = create_state()
    state["cycleId"] = f"CYC-{int(time.time() * 1000):X}"
    state["timestamp"] = datetime.now(timezone.utc).isoformat()

    scenario_config = generate_scenario(scenario) if isinstance(scenario, str) else scenario
    state["data"]["deliveries"] = generate_deliveries(delivery_count, scenario_config)
    state["data"]["inventory"] = generate_inventory(scenario_config)
    state["data"]["drivers"] = generate_drivers(driver_count, scenario_config)
    state["data"]["weather"] = generate_weather(scenario_config)

    label = scenario if isinstance(scenario, str) else "custom"
    log_event(state, "System", f"Cycle started: scenario={label}")
    return state


# ── Standard run (stateless, no HITL) — used by CLI and what-if ───────────────

def run_cycle(
    scenario: str | dict = "normal",
    delivery_count: int = 25,
    driver_count: int = 24,
    on_step=None,
) -> SeeWeeSState:
    """
    Run one full planning cycle and return the final state.
    Does NOT use the checkpointer — HumanCheckpoint is a pass-through.
    """
    state = _make_initial_state(scenario, delivery_count, driver_count)
    state["flow"]["status"] = "running"

    graph = build_graph()   # stateless graph

    if on_step:
        for chunk in graph.stream(state, stream_mode="updates"):
            node_name = next(iter(chunk))
            on_step({"node": node_name, "timestamp": datetime.now(timezone.utc).isoformat()})
        state = graph.invoke(state)
    else:
        state = graph.invoke(state)

    if state["flow"]["status"] == "running":
        state["flow"]["status"] = "completed"

    log_event(state, "System", f"Cycle completed: status={state['flow']['status']}")
    return state


# ── HITL run — used by the web server /api/run ─────────────────────────────────

def run_cycle_hitl(
    scenario: str | dict = "normal",
    delivery_count: int = 25,
    driver_count: int = 24,
) -> tuple[SeeWeeSState, bool, str]:
    """
    Run one cycle with the MemorySaver checkpointer so it can be interrupted.

    Returns:
        (state, is_interrupted, thread_id)

        is_interrupted=True means HumanCheckpoint called interrupt() and the
        graph is paused awaiting manager input. The caller should return the
        thread_id to the client so it can call resume_cycle().
    """
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    state = _make_initial_state(scenario, delivery_count, driver_count)
    state["flow"]["status"] = "running"
    state["flow"]["hitl_enabled"] = True   # enables interrupt() in HumanCheckpoint

    graph = _get_memory_graph()
    state = graph.invoke(state, config=config)

    # Check if the graph paused at HumanCheckpoint
    snapshot = graph.get_state(config)
    is_interrupted = bool(snapshot.next)  # non-empty tuple means waiting for resume

    if not is_interrupted and state["flow"]["status"] == "running":
        state["flow"]["status"] = "completed"
        log_event(state, "System", f"Cycle completed: status={state['flow']['status']}")

    return state, is_interrupted, thread_id


def resume_cycle(thread_id: str, decision: str) -> SeeWeeSState:
    """
    Resume a paused graph after manager decision.

    Args:
        thread_id: the id returned by run_cycle_hitl().
        decision:  "approved" or "rejected".

    Returns the final state after the graph completes.
    """
    config = {"configurable": {"thread_id": thread_id}}
    graph = _get_memory_graph()
    state = graph.invoke(Command(resume=decision), config=config)

    log_event(state, "System", f"Cycle resumed: decision={decision}, status={state['flow']['status']}")
    return state


# ── What-if comparison (stateless, parallel scenarios) ────────────────────────

def run_what_if(
    scenarios: dict[str, str | dict] | None = None,
    delivery_count: int = 25,
    driver_count: int = 24,
) -> list[dict]:
    if scenarios is None:
        scenarios = {
            "Baseline": "normal",
            "Demand Surge (+40%)": "demandSurge",
            "Warehouse Closure": "warehouseClosure",
            "Driver Shortage (−30%)": "driverShortage",
            "Severe Weather": "severeWeather",
            "Compound Crisis": "compound",
        }

    results = []
    for name, cfg in scenarios.items():
        state = run_cycle(scenario=cfg, delivery_count=delivery_count, driver_count=driver_count)
        results.append({
            "scenario": name,
            "kpis": state["report"].get("kpis", {}),
            "riskScore": state["audit"]["riskScore"],
            "status": state["report"].get("status", ""),
            "topRisks": state["report"].get("topRisks", []),
            "recommendations": state["report"].get("recommendations", []),
            "correctionLoops": state["audit"]["correctionCount"],
        })

    return results
