"""
AuditAgent — Phase 3 of the LangGraph pipeline.

Rule-based layer:
  - Cold-chain compliance: cold medicines must go to cold-chain vehicles
  - Driver hour limits: projected total must not exceed 12 hours
  - Route time limits: no single route > 180 minutes
  - Unassigned critical deliveries: flagged as critical (downgraded to warning after 2 attempts)
  - Warehouse capacity overflow: warehouses > 95% trigger a warning
  - Composite risk score: weighted sum of anomalies, violations, and weather

LLM layer (Groq / Claude):
  - Triggered only when violations are found
  - Generates numbered, route-specific correction steps for the PlannerAgent
  - Stored in state.audit.llm_correction_guidance for the next planner revision

Routing function:
  - auditDecision(state) → "PlannerAgent" | "ReportAgent"
  - "PlannerAgent" is returned when violations remain and retries < MAX_CORRECTIONS
  - After MAX_CORRECTIONS the plan is escalated and routed to ReportAgent anyway
"""

from core.state import SeeWeeSState, log_event
from core.llm import call_llm
from core.prompts import AUDIT_CORRECTION_SYSTEM, AUDIT_CORRECTION_USER

MAX_CORRECTIONS = 3
RISK_THRESHOLD_HUMAN = 65
MAX_DRIVER_HOURS = 12
MAX_ROUTE_TIME = 180  # minutes


def audit_agent(state: SeeWeeSState) -> SeeWeeSState:
    correction_count = state["audit"]["correctionCount"]
    log_event(state, "AuditAgent", f"started (correction #{correction_count})")

    routes = state["plan"]["routes"]
    allocations = state["plan"]["allocations"]
    drivers = state["data"]["drivers"]
    violations: list[dict] = []

    # ── 1. Cold-chain compliance ───────────────────────────────────────────────
    for r in routes:
        if r.get("hasColdChain") and not r.get("driverHasColdChain") and r.get("driver") != "UNASSIGNED":
            violations.append({
                "type": "cold_chain_violation",
                "severity": "critical",
                "route": r["id"],
                "region": r["region"],
                "detail": (
                    f"Route {r['id']}: cold-chain medicine assigned to non-cold-chain "
                    f"vehicle (driver {r['driver']})"
                ),
            })

    # ── 2. Driver hour limits ──────────────────────────────────────────────────
    driver_load: dict[str, float] = {}
    for r in routes:
        if r.get("driver") == "UNASSIGNED":
            continue
        driver_load[r["driver"]] = driver_load.get(r["driver"], 0) + r.get("estimatedTime", 0)

    for d in drivers:
        route_minutes = driver_load.get(d["id"], 0)
        total_hours = d.get("hoursWorked", 0) + route_minutes / 60
        if total_hours > MAX_DRIVER_HOURS:
            violations.append({
                "type": "driver_hours_exceeded",
                "severity": "warning",
                "route": "multiple",
                "region": d.get("region"),
                "detail": (
                    f"Driver {d['id']} ({d.get('name')}): projected "
                    f"{total_hours:.1f}h exceeds {MAX_DRIVER_HOURS}h limit"
                ),
            })

    # ── 3. Route time limits ───────────────────────────────────────────────────
    for r in routes:
        if r.get("estimatedTime", 0) > MAX_ROUTE_TIME:
            violations.append({
                "type": "route_too_long",
                "severity": "warning",
                "route": r["id"],
                "region": r["region"],
                "detail": f"Route {r['id']}: {r['estimatedTime']}min exceeds {MAX_ROUTE_TIME}min limit",
            })

    # ── 4. Unassigned critical deliveries ────────────────────────────────────
    for r in routes:
        if r.get("driver") == "UNASSIGNED" and r.get("maxPriority") == "critical":
            severity = "warning" if correction_count >= 2 else "critical"
            violations.append({
                "type": "unassigned_critical",
                "severity": severity,
                "route": r["id"],
                "region": r["region"],
                "detail": (
                    f"Route {r['id']}: critical deliveries have no assigned driver"
                    + (" (escalated to manual assignment)" if severity == "warning" else "")
                ),
            })

    # ── 5. Warehouse capacity overflow ────────────────────────────────────────
    for alloc in allocations:
        if alloc.get("capacityUsed", 0) > 95:
            violations.append({
                "type": "warehouse_overflow",
                "severity": "warning",
                "route": "N/A",
                "region": alloc["region"],
                "detail": (
                    f"{alloc.get('warehouse', alloc['region'])} at "
                    f"{alloc['capacityUsed']}% — risk of stockout or overflow"
                ),
            })

    # ── 6. Composite risk score ────────────────────────────────────────────────
    risk_score = 0
    critical_anomalies = sum(
        1 for a in state["data"]["anomalies"] if a.get("severity") == "critical"
    )
    risk_score += critical_anomalies * 12

    critical_v = [v for v in violations if v.get("severity") == "critical"]
    warning_v = [v for v in violations if v.get("severity") == "warning"]
    risk_score += len(critical_v) * 15 + len(warning_v) * 5

    weather_risks = sum(1 for w in state["data"]["weather"] if w.get("riskLevel") == "high")
    risk_score += weather_risks * 10

    if state["data"]["dataQuality"] < 80:
        risk_score += 10

    unassigned = sum(1 for r in routes if r.get("driver") == "UNASSIGNED")
    risk_score += unassigned * 8
    risk_score = min(100, risk_score)

    # ── 7. Decision flags ─────────────────────────────────────────────────────
    passed = len(critical_v) == 0
    requires_human = risk_score >= RISK_THRESHOLD_HUMAN

    state["audit"]["violations"] = violations
    state["audit"]["riskScore"] = risk_score
    state["audit"]["passed"] = passed
    state["audit"]["requiresHumanApproval"] = requires_human

    if not passed:
        state["audit"]["correctionCount"] += 1
        count = state["audit"]["correctionCount"]
        if count < MAX_CORRECTIONS:
            # Prepare the planner for the next revision
            state["plan"]["revision"] += 1
        else:
            # Retries exhausted — mark escalation so ReportAgent knows
            state["flow"]["status"] = "escalated"

    # ── 8. LLM layer: generate correction guidance when violations exist ───────
    llm_guidance = ""
    if violations and not passed:
        try:
            critical_lines = "\n".join(
                f"  - {v['detail']}" for v in critical_v
            ) or "  None."
            warning_lines = "\n".join(
                f"  - {v['detail']}" for v in warning_v[:5]
            ) or "  None."

            llm_guidance = call_llm(
                system=AUDIT_CORRECTION_SYSTEM,
                user=AUDIT_CORRECTION_USER.format(
                    revision=state["plan"]["revision"],
                    critical_violations=critical_lines,
                    warning_violations=warning_lines,
                ),
            )
        except Exception as e:
            print(f"[AuditAgent] LLM call failed: {e}")

    state["audit"]["llm_correction_guidance"] = llm_guidance

    log_event(
        state, "AuditAgent",
        f"completed: {len(violations)} violations ({len(critical_v)} critical), "
        f"risk={risk_score}, passed={passed}, human_approval={requires_human}"
        + (" | LLM correction guidance generated" if llm_guidance else ""),
    )
    return state


def audit_decision(state: SeeWeeSState) -> str:
    """
    Pure routing function for LangGraph's conditional edge.
    Must NOT mutate state — LangGraph passes a snapshot here, not the live dict.
    All state writes happen inside audit_agent() above.
    """
    if not state["audit"]["passed"] and state["audit"]["correctionCount"] < MAX_CORRECTIONS:
        return "PlannerAgent"
    return "ReportAgent"
