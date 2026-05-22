# SeeWeeS AI Agents — Technical & Business Report

## Executive Summary

SeeWeeS is a specialty pharmaceutical distribution company operating time-critical medical deliveries across multiple regions. The original reporting system was a linear pipeline that ingested a CSV and a PDF, then generated a single HTML report. It could not handle data gaps, correct its own plans, or pause for human oversight when risk was high.

This project transforms that prototype into a **robust multi-agent system** built on LangGraph. The system now:

- Detects and explains data quality issues using an LLM (Groq qwen/qwen3-32b)
- Plans dispatch routes with cold-chain compliance and cross-region driver allocation
- Self-corrects plans that violate safety constraints through a cyclic audit loop (up to 3 revisions)
- Pauses for manager approval when the composite risk score exceeds 65/100 using LangGraph's native `interrupt()` feature
- Simulates six disruption scenarios (demand surge, warehouse closure, driver shortage, severe weather, compound crisis) for what-if analysis
- Presents results in an interactive web dashboard with KPI cards, risk tables, route breakdowns, and audit trails

**Stakeholder:** Operations leadership at SeeWeeS who need a decision-ready daily dispatch briefing that flags the highest risks and proposes concrete actions.

**Operational pain point:** The linear system passed bad plans to leadership without checking them against safety protocols, could not recover from data gaps, and had no mechanism to escalate high-risk situations for human review.

---

## Key Assumptions

| Area | Assumption |
|---|---|
| Cold-chain compliance | Medicines marked `requiresColdChain=true` must be assigned only to drivers with `hasColdChainVehicle=true`. Any mismatch is a critical violation. |
| Driver hours | Maximum 12 hours per driver per cycle. Projected hours = `hoursWorked + routeMinutes / 60`. |
| Route time cap | Routes exceeding 180 minutes are flagged as a warning. |
| Delay threshold | Deliveries with `estimatedDelay > 30 min` are counted as "delayed" for service level KPIs. Critical priority deliveries are flagged when delay > 15 min. |
| Risk score escalation | Composite risk ≥ 65/100 triggers human approval. Risk ≥ 3 audit retries triggers escalation. |
| Data augmentation | Missing weight fields are filled using per-drug historical averages (e.g., Insulin: 0.05 kg/unit, Blood Products: 0.25 kg/unit). Augmented fields reduce the data quality score. |
| Simulated defaults | If inventory, driver, or weather data is not uploaded, realistic defaults are simulated so the pipeline can still produce a meaningful plan. |
| Region splitting | Regions with more than 200 deliveries are automatically split into sub-zones (A, B, C…) to keep route sizes manageable. |

---

## Technical Methodology

### LangGraph Architecture

The system uses a **directed `StateGraph`** from LangGraph 1.2.1 with five nodes and a cyclic audit loop.

```
DataAgent → PlannerAgent → AuditAgent ─┬─ (pass / escalate) ──→ ReportAgent → HumanCheckpoint → END
                                        └─ (retry, ≤ 3×) ─────→ PlannerAgent
```

**State:** A single `SeeWeeSState` TypedDict is shared across all nodes. Sub-dicts (`data`, `plan`, `audit`, `report`, `flow`) are written by their respective agents and read downstream. This mirrors LangGraph's official state-passing pattern.

**Conditional edge:** `audit_decision()` is a pure routing function (no state mutations — LangGraph passes edge functions a state snapshot, not the live dict). All state mutations during the audit cycle happen inside `audit_agent()` itself:
- If `correctionCount < 3` and violations remain: increment `plan.revision`, return `"PlannerAgent"`
- If `correctionCount >= 3`: set `flow.status = "escalated"`, return `"ReportAgent"`
- Otherwise: return `"ReportAgent"`

**MemorySaver checkpointer:** Used only for interactive dashboard runs (`run_cycle_hitl`). The CLI and upload pipeline use a stateless graph. The `hitl_enabled` flag in state gates the `interrupt()` call so stateless runs never crash.

### Enhancement #1 — Self-Correction & Quality Assurance (Audit Loop)

**Implementation:**

The `AuditAgent` checks five constraint categories in order:

1. Cold-chain compliance (critical)
2. Driver hour limits — `hoursWorked + routeMinutes/60 > 12h` (warning)
3. Route time — `estimatedTime > 180 min` (warning)
4. Unassigned critical deliveries (critical, downgraded to warning after 2 retries)
5. Warehouse capacity overflow > 95% (warning)

A composite risk score (0–100) is computed from: critical anomalies × 12, critical violations × 15, warning violations × 5, high-weather regions × 10, data quality penalty (+10 if < 80%), unassigned routes × 8.

**LLM layer:** When violations are found, `AuditAgent` calls `qwen/qwen3-32b` with the violation list and asks for numbered, route-specific correction steps. These appear in the dashboard's audit trail and guide the next planner revision.

**PlannerAgent revision logic:** On `revision > 0`, the planner borrows up to 3 available drivers from adjacent regions to resolve unassigned-driver violations. Cold-chain batches are always matched to cold-chain drivers first.

### Enhancement #2 — What-if Scenario Simulation

Six named scenarios modify the data generator's parameters:

| Scenario | Modification |
|---|---|
| Normal | Baseline — no disruptions |
| Demand Surge | Region A deliveries × 1.4 |
| Warehouse Closure | Warehouse D: `operational=False`, `capacityUsed=100` |
| Driver Shortage | 30% of drivers set to `unavailable` |
| Severe Weather | Regions B and C: `riskLevel=high`, storm/snow conditions |
| Compound Crisis | Demand +30% in Region A + 20% driver shortage + Region C severe weather |

The dashboard's **Compare All** mode runs all six scenarios and displays a side-by-side bar chart of risk scores, service levels, and correction loops.

### Enhancement #4 — Human-in-the-Loop Checkpoint

**Implementation using LangGraph `interrupt()`:**

A dedicated `HumanCheckpoint` node sits between `ReportAgent` and `END`. When `audit.requiresHumanApproval = True` (risk ≥ 65) and the run was initiated from the web dashboard (HITL mode), the node calls:

```python
decision = interrupt({
    "message": "High-risk dispatch plan requires manager approval",
    "riskScore": risk,
    "topRisks": [...]
})
```

LangGraph saves the full state to a `MemorySaver` checkpointer keyed by a per-run `thread_id`. The `graph.invoke()` call returns the state (including the completed report) and the server detects the interrupt via `graph.get_state(config).next`.

The client receives `{ awaitingApproval: true, threadId: "..." }` and displays an approval banner over the results. When the manager clicks **Approve** or **Reject**, the client posts to `/api/approve`, which calls:

```python
graph.invoke(Command(resume=decision), config={"configurable": {"thread_id": thread_id}})
```

The graph resumes from the checkpoint, the `HumanCheckpoint` node receives the decision, sets `flow.status = "approved_by_human"` or `"rejected_by_human"`, and the final state is returned to the dashboard.

### LLM Integration — Data Quality Reasoning

`DataAgent` uses Groq `qwen/qwen3-32b` after rule-based anomaly detection. The prompt asks the model to reason about:

1. **Root cause** of the top anomalies in operational terms
2. **Planning confidence** (High / Medium / Low) given the current data quality score
3. **Single most important data collection fix** needed

This output is displayed in the dashboard's Executive Summary section and stored in `state.data.llm_assessment`. The rule-based pipeline is unaffected — if no API key is configured, the LLM assessment is simply omitted.

**Groq ↔ Claude fallback:** If Groq hits a rate limit (HTTP 429), the system automatically retries with Claude `claude-haiku-4-5-20251001`. Both are accessed via LangChain's `ChatGroq` and `ChatAnthropic` wrappers.

**Qwen3 output hygiene:** The model sometimes emits `<think>...</think>` reasoning blocks and word-count compliance notes (`(119 words)`). Both are stripped with regex before the text reaches any agent or the dashboard.

---

## Results & Validation

### Example outputs

**Normal scenario:**
- Service Level: 72% (critical — target 95%)
- Delay Risk: 28% (red — target < 5%)
- Composite Risk: 16/100 (low)
- LLM assessment: *"Delays likely stem from unrecorded external factors (e.g., traffic, weather)… Add mandatory 'delay root cause' field."*

**Severe Weather scenario (Regions B + C disrupted):**
- Composite Risk: 100/100 → triggers Human Approval checkpoint
- Correction Loops: 2 (planner revised twice before audit passed)
- LLM executive summary correctly named specific regions and weather conditions and recommended contingency route activation with 20-minute buffers

**Compound Crisis scenario:**
- Risk: 85/100 → PENDING HUMAN APPROVAL
- LLM correction guidance after audit failure: specific route IDs and driver reassignments for the next revision

### Validation strategy

- **Constraint correctness:** Each audit rule is independently unit-testable. The cold-chain check, hour limit, and unassigned-critical check were verified by seeding states with known violations and confirming `audit.violations` matches expectations.
- **Graph routing:** The conditional edge was verified to loop exactly 3× on a state with persistent critical violations (correctionCount maxes at 3, status set to "escalated").
- **HITL flow:** Interrupt/resume was tested programmatically: `run_cycle_hitl()` returns `is_interrupted=True` on high-risk scenarios, and `resume_cycle(thread_id, "approved")` produces `flow.status = "approved_by_human"`.
- **LLM outputs:** Spot-checked across normal, severe weather, and compound scenarios. Model responses are contextually accurate and correctly reference specific regions, risk figures, and operational thresholds from the data.

---

## Limitations & Next Steps

| Limitation | Next Step |
|---|---|
| Simulated data only — no connection to real SeeWeeS systems | Integrate with ERP / TMS APIs for live delivery and inventory data |
| MemorySaver is in-process (lost on server restart) | Replace with `SqliteSaver` or `PostgresSaver` for production durability |
| LLM calls are synchronous — slow on compound scenarios (3+ audit loops = 6+ LLM calls) | Move LLM calls to async tasks; add response caching for identical anomaly signatures |
| PDF playbook not yet integrated | Add RAG retrieval (as in the original starter) so audit rules are grounded in the actual SeeWeeS Dispatch Playbook |
| What-if scenarios are pre-defined | Allow users to specify custom disruption parameters (e.g., "Region C warehouse at 100% + 40% demand spike") directly from the dashboard |
| Human rejection has no re-planning path | On rejection, route back to PlannerAgent with the manager's rejection reason injected into state |
| Single-region drivers — no real geospatial routing | Integrate a routing engine (OSRM or Google Maps) for actual drive-time estimates |
