from typing import Any
from typing_extensions import TypedDict


class FlowState(TypedDict):
    currentAgent: str | None
    status: str          # idle | running | completed | escalated | approved_by_human | rejected_by_human
    history: list[dict]  # ordered agent execution log
    errors: list[str]
    hitl_enabled: bool   # True only when a MemorySaver checkpointer is attached (run_cycle_hitl)


class DataState(TypedDict):
    deliveries: list[dict]
    inventory: list[dict]
    drivers: list[dict]
    weather: list[dict]
    anomalies: list[dict]
    dataQuality: int     # 0–100
    missingFields: list[str]
    augmented: list[str] # fields that were simulated
    llm_assessment: str  # LLM narrative on data quality and confidence


class PlanState(TypedDict):
    routes: list[dict]
    allocations: list[dict]
    contingencies: list[dict]
    revision: int        # how many times re-planned
    revisionReasons: list[str]


class AuditState(TypedDict):
    passed: bool
    violations: list[dict]
    riskScore: int       # 0–100
    requiresHumanApproval: bool
    correctionCount: int
    llm_correction_guidance: str  # LLM-generated steps for the planner to fix violations
    humanDecision: str            # "approved" | "rejected" | "" — set by HumanCheckpoint


class ReportState(TypedDict):
    kpis: dict[str, Any]
    topRisks: list[dict]
    recommendations: list[str]
    auditTrail: list[dict]
    summary: str


class SeeWeeSState(TypedDict):
    cycleId: str | None
    timestamp: str | None
    region: str
    data: DataState
    plan: PlanState
    audit: AuditState
    report: ReportState
    flow: FlowState


def create_state() -> SeeWeeSState:
    return SeeWeeSState(
        cycleId=None,
        timestamp=None,
        region="ALL",
        data=DataState(
            deliveries=[],
            inventory=[],
            drivers=[],
            weather=[],
            anomalies=[],
            dataQuality=0,
            missingFields=[],
            augmented=[],
            llm_assessment="",
        ),
        plan=PlanState(
            routes=[],
            allocations=[],
            contingencies=[],
            revision=0,
            revisionReasons=[],
        ),
        audit=AuditState(
            passed=False,
            violations=[],
            riskScore=0,
            requiresHumanApproval=False,
            correctionCount=0,
            llm_correction_guidance="",
            humanDecision="",
        ),
        report=ReportState(
            kpis={},
            topRisks=[],
            recommendations=[],
            auditTrail=[],
            summary="",
        ),
        flow=FlowState(
            currentAgent=None,
            status="idle",
            history=[],
            errors=[],
            hitl_enabled=False,
        ),
    )


def log_event(state: SeeWeeSState, agent: str, event: str) -> None:
    from datetime import datetime, timezone
    state["flow"]["history"].append({
        "agent": agent,
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
