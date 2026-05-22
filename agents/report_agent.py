"""
ReportAgent — Final phase of the LangGraph pipeline.

Rule-based layer:
  - Calculates 6 KPIs: delay risk, utilisation, service level, correction loops,
    data quality, composite risk score
  - Assembles top-5 risks from anomalies, audit violations, and contingencies
  - Generates templated recommendations for drivers, weather, warehouses, and data

LLM layer (Groq / Claude):
  - Writes an executive narrative summary that replaces the rule-assembled string
  - The narrative synthesises all KPIs and risks into a 3-paragraph C-suite briefing
  - Stored in state.report.summary
"""

from core.state import SeeWeeSState, log_event
from core.llm import call_llm
from core.prompts import EXECUTIVE_SUMMARY_SYSTEM, EXECUTIVE_SUMMARY_USER


def report_agent(state: SeeWeeSState) -> SeeWeeSState:
    log_event(state, "ReportAgent", "started")

    deliveries = state["data"]["deliveries"]
    anomalies = state["data"]["anomalies"]
    data_quality = state["data"]["dataQuality"]
    routes = state["plan"]["routes"]
    contingencies = state["plan"]["contingencies"]
    violations = state["audit"]["violations"]
    risk_score = state["audit"]["riskScore"]
    correction_count = state["audit"]["correctionCount"]
    requires_human = state["audit"]["requiresHumanApproval"]
    passed = state["audit"]["passed"]

    total_deliveries = len(deliveries)

    # ── 1. Data presence flags (suppress misleading KPIs when data is absent) ─
    has_real_delay_data = any(d.get("estimatedDelay", 0) > 0 for d in deliveries)

    # ── 2. KPI calculations ───────────────────────────────────────────────────
    delayed = sum(1 for d in deliveries if (d.get("estimatedDelay") or 0) > 30)
    on_time = total_deliveries - delayed
    delay_risk = round(delayed / total_deliveries * 100, 1) if total_deliveries else 0
    service_level = round(on_time / total_deliveries * 100, 1) if total_deliveries else 0

    assigned_routes = sum(1 for r in routes if r.get("driver") != "UNASSIGNED")
    total_routes = len(routes)
    utilization = round(assigned_routes / total_routes * 100, 1) if total_routes else 0

    no_data_status = "no_data"

    kpis = {
        "delayRisk": {
            "value": delay_risk,
            "unit": "%",
            "status": no_data_status if not has_real_delay_data
                      else ("green" if delay_risk < 5 else "amber" if delay_risk < 12 else "red"),
            "label": "Delivery Delay Risk",
            "note": "No delay data provided — all values are defaults" if not has_real_delay_data else None,
        },
        "utilization": {
            "value": utilization,
            "unit": "%",
            "status": "optimal" if 70 <= utilization <= 90
                      else ("underused" if utilization < 70 else "strained"),
            "label": "Resource Utilization",
        },
        "serviceLevel": {
            "value": service_level,
            "unit": "%",
            "status": no_data_status if not has_real_delay_data
                      else ("target" if service_level >= 95 else "watch" if service_level >= 90 else "critical"),
            "label": "Service Level",
            "note": "Based on default delay values — not actual performance" if not has_real_delay_data else None,
        },
        "correctionLoops": {
            "value": correction_count,
            "unit": "",
            "status": "normal" if correction_count <= 1 else "warning" if correction_count == 2 else "escalation",
            "label": "Correction Loops",
        },
        "dataQuality": {
            "value": data_quality,
            "unit": "%",
            "status": "good" if data_quality >= 90 else "fair" if data_quality >= 70 else "poor",
            "label": "Data Quality",
        },
        "riskScore": {
            "value": risk_score,
            "unit": "/100",
            "status": "low" if risk_score < 30 else "moderate" if risk_score < 65 else "high",
            "label": "Composite Risk",
        },
    }

    # ── 3. Top risks ──────────────────────────────────────────────────────────
    top_risks: list[dict] = []

    for a in [a for a in anomalies if a.get("severity") == "critical"][:3]:
        top_risks.append({
            "risk": a["detail"],
            "severity": "High",
            "type": a["type"],
            "region": a.get("region"),
        })

    for v in [v for v in violations if v.get("severity") == "critical"][:2]:
        top_risks.append({
            "risk": v["detail"],
            "severity": "High",
            "type": v["type"],
            "region": v.get("region"),
        })

    if contingencies:
        top_risks.append({
            "risk": f"{len(contingencies)} routes require contingency plans",
            "severity": "Medium",
            "type": "contingency_needed",
            "region": "Multiple",
        })

    # ── 4. Recommendations ────────────────────────────────────────────────────
    recommendations: list[dict] = []

    shortage_regions = [a["region"] for a in anomalies if a["type"] == "driver_shortage"]
    if shortage_regions:
        recommendations.append({
            "priority": "immediate",
            "action": f"Reassign drivers to {', '.join(shortage_regions)} from regions with surplus capacity",
            "rationale": "Critical deliveries may be delayed without driver reallocation",
        })

    weather_regions = [a["region"] for a in anomalies if a["type"] == "weather_disruption"]
    if weather_regions:
        recommendations.append({
            "priority": "immediate",
            "action": f"Activate contingency routes for {', '.join(weather_regions)}. Add 20-minute buffer.",
            "rationale": "Severe weather conditions increase delay risk and road safety concerns",
        })

    capacity_warnings = [a for a in anomalies if a["type"] == "capacity_warning" and a["severity"] == "critical"]
    if capacity_warnings:
        recommendations.append({
            "priority": "short-term",
            "action": f"Initiate overflow protocol for {', '.join(a['record'] for a in capacity_warnings)}. Redirect to backup warehouses.",
            "rationale": "Warehouses above 95% risk stockout and processing delays",
        })

    if data_quality < 80:
        recommendations.append({
            "priority": "short-term",
            "action": "Investigate data pipeline for missing fields. Augmented data was used this cycle.",
            "rationale": f"Data quality at {data_quality}% — decisions carry higher uncertainty",
        })

    if service_level < 90 and has_real_delay_data:
        recommendations.append({
            "priority": "strategic",
            "action": "Schedule a review to address declining service level trend",
            "rationale": f"Service level at {service_level}% is below the 90% minimum threshold",
        })

    if requires_human:
        recommendations.append({
            "priority": "immediate",
            "action": "HUMAN APPROVAL REQUIRED — risk score exceeds threshold. Review before execution.",
            "rationale": f"Composite risk score is {risk_score}/100, above the {65} auto-approval limit",
        })

    # ── 5. Audit trail ────────────────────────────────────────────────────────
    audit_trail = [
        {"agent": h["agent"], "event": h["event"], "timestamp": h["timestamp"]}
        for h in state["flow"]["history"]
    ]

    # ── 6. Rule-based summary (fallback) ──────────────────────────────────────
    status_word = (
        "ESCALATED" if state["flow"]["status"] == "escalated"
        else "PENDING HUMAN APPROVAL" if requires_human
        else "APPROVED" if passed
        else "REVIEW NEEDED"
    )

    rule_summary = (
        f"SeeWeeS Planning Cycle {state['cycleId']} — Status: {status_word}\n\n"
        f"Processed {total_deliveries} deliveries across {len(routes)} routes.\n"
        f"Delay risk: {delay_risk}% | Service level: {service_level}% | Risk score: {risk_score}/100\n"
        f"Correction loops: {correction_count} | Data quality: {data_quality}%\n\n"
        f"Top action: {recommendations[0]['action'] if recommendations else 'No immediate action required.'}"
    )

    # ── 7. LLM layer: write executive narrative ───────────────────────────────
    summary = rule_summary  # default; LLM replaces this if available
    try:
        risk_lines = "\n".join(
            f"  - [{r['severity']}] {r['risk']}" for r in top_risks[:4]
        ) or "  None identified."
        rec_lines = "\n".join(
            f"  - [{r['priority'].upper()}] {r['action']}" for r in recommendations[:3]
        ) or "  No immediate action required."

        llm_summary = call_llm(
            system=EXECUTIVE_SUMMARY_SYSTEM,
            user=EXECUTIVE_SUMMARY_USER.format(
                cycle_id=state["cycleId"],
                status=status_word,
                service_level=service_level,
                delay_risk=delay_risk,
                utilization=utilization,
                risk_score=risk_score,
                data_quality=data_quality,
                correction_loops=correction_count,
                top_risks=risk_lines,
                top_recommendations=rec_lines,
            ),
        )
        if llm_summary:
            summary = llm_summary
    except Exception as e:
        print(f"[ReportAgent] LLM call failed: {e}")

    # ── 8. Write to state ─────────────────────────────────────────────────────
    from datetime import datetime, timezone
    state["report"] = {
        "kpis": kpis,
        "topRisks": top_risks[:5],
        "recommendations": recommendations,
        "auditTrail": audit_trail,
        "summary": summary,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": status_word,
    }

    log_event(
        state, "ReportAgent",
        f"completed: {len(top_risks)} risks, {len(recommendations)} recommendations"
        + (" | LLM summary written" if summary != rule_summary else ""),
    )
    return state
