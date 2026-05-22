"""
HumanCheckpointAgent — Enhancement #4 from the project spec.

Sits between ReportAgent and END in the graph. When the audit's composite
risk score exceeds the approval threshold (≥ 65/100), this node calls
LangGraph's interrupt() to genuinely pause the graph. The dashboard shows
the full report to a manager who can Approve or Reject before the final
status is committed.

When approval is not required the node is a transparent pass-through —
it logs one line and immediately returns state unchanged.

Resume flow (called from server.py via Command(resume=decision)):
  "approved" → flow.status = "approved_by_human"
  "rejected"  → flow.status = "rejected_by_human"
"""

from langgraph.types import interrupt
from core.state import SeeWeeSState, log_event


def human_checkpoint_agent(state: SeeWeeSState) -> SeeWeeSState:
    requires_approval = state["audit"]["requiresHumanApproval"]

    if not requires_approval:
        log_event(state, "HumanCheckpoint", "Risk within auto-approve threshold — passed through")
        return state

    if not state["flow"].get("hitl_enabled", False):
        # No checkpointer attached (CLI / upload / what-if run) — flag it but continue.
        log_event(state, "HumanCheckpoint", f"HITL disabled: risk {state['audit']['riskScore']}/100 flagged for manual review")
        return state

    risk = state["audit"]["riskScore"]
    report_status = state["report"].get("status", "")
    top_risks = state["report"].get("topRisks", [])[:3]

    log_event(
        state, "HumanCheckpoint",
        f"PAUSED: risk score {risk}/100 exceeds threshold — awaiting manager decision",
    )

    # ── Genuine LangGraph interrupt ───────────────────────────────────────────
    # Execution stops here. The graph state is saved to the MemorySaver
    # checkpointer. The caller receives the state as-is (report already
    # generated). The graph resumes when the server calls
    #   graph.invoke(Command(resume="approved"|"rejected"), config=config)
    decision: str = interrupt({
        "message": "High-risk dispatch plan requires manager approval before execution",
        "riskScore": risk,
        "reportStatus": report_status,
        "topRisks": [r.get("risk", "") for r in top_risks],
    })

    # ── Resume path ───────────────────────────────────────────────────────────
    state["audit"]["humanDecision"] = decision

    if decision == "approved":
        state["flow"]["status"] = "approved_by_human"
        log_event(state, "HumanCheckpoint", "Plan APPROVED by manager — proceeding to execution")
    else:
        state["flow"]["status"] = "rejected_by_human"
        log_event(state, "HumanCheckpoint", "Plan REJECTED by manager — escalated for replanning")

    return state
