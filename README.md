# SeeWeeS — LangGraph Implementation

A multi-agent medical logistics dispatch system built on **real LangGraph** with **Groq qwen/qwen3-32b** as the reasoning engine. The same web dashboard from the original prototype runs unchanged — only the backend has been replaced with Python LangGraph.

## Architecture

```
DataAgent → PlannerAgent → AuditAgent ─┬─(pass / escalate)──→ ReportAgent → HumanCheckpoint → END
                                        └─(retry, ≤ 3×) ─────→ PlannerAgent
```

| Node | Role | LLM call? |
|---|---|---|
| **DataAgent** | Ingests data, fills missing weight fields, detects anomalies, scores quality | Yes — data quality narrative + planning confidence |
| **PlannerAgent** | Builds routes, matches cold-chain medicines to cold-chain vehicles, borrows drivers cross-region on retries | No — deterministic constraint solver |
| **AuditAgent** | Checks 5 safety constraints, calculates composite risk score (0–100), triggers correction loop | Yes — numbered correction steps for the next planner revision |
| **ReportAgent** | Calculates 6 KPIs, surfaces top risks, assembles recommendations | Yes — C-suite executive narrative summary |
| **HumanCheckpoint** | Calls LangGraph `interrupt()` when risk ≥ 65 to pause for manager approval | No |

### LangGraph features used

| Feature | Where |
|---|---|
| `StateGraph` + `TypedDict` state | `system.py` / `core/state.py` |
| `add_conditional_edges` | AuditAgent → PlannerAgent (retry) or ReportAgent (pass) |
| `interrupt()` | HumanCheckpoint node |
| `MemorySaver` checkpointer | Persists state between interrupt and resume |
| `Command(resume=...)` | Manager approval/rejection resumes the graph |
| `graph.stream()` | CLI step-by-step logging |

### Enhancements implemented

1. **Self-Correction & QA Audit Loop** — AuditAgent checks the plan against safety constraints. On failure, a conditional edge loops back to PlannerAgent (up to 3×). The AuditAgent calls an LLM to generate specific, route-level correction instructions for each revision.

2. **What-if Scenario Simulation** — Six disruption scenarios (demand surge, warehouse closure, driver shortage, severe weather, compound crisis) run in the dashboard's comparison mode. All scenarios can be compared side-by-side in a bar chart.

3. **Human-in-the-Loop Checkpoint** — When composite risk exceeds 65/100, LangGraph's `interrupt()` genuinely pauses execution. The manager reviews the full report in the dashboard and clicks Approve or Reject before the final status is committed. Uses `MemorySaver` + `Command(resume=...)` for state persistence across the pause.

---

## Setup

**Requirements:** Python 3.11+

```bash
cd lngrph-implementation

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env — add your GROQ_API_KEY (free at https://console.groq.com)
# Add ANTHROPIC_API_KEY as fallback (optional)
```

## Run the dashboard

```bash
uvicorn server:app --port 3200 --reload
```

Open **http://localhost:3200**

## CLI demo

```bash
python run_demo.py normal
python run_demo.py demandSurge
python run_demo.py warehouseClosure
python run_demo.py driverShortage
python run_demo.py severeWeather
python run_demo.py compound
```

## API endpoints

| Method | Path | What it does |
|---|---|---|
| `POST` | `/api/run` | Run a named scenario. Returns `state`, `awaitingApproval`, `threadId` |
| `POST` | `/api/approve` | Resume a paused graph with `{threadId, decision}` (`"approved"` or `"rejected"`) |
| `POST` | `/api/upload` | Upload CSV / Excel / JSON files and run analysis |
| `POST` | `/api/whatif` | Run all 6 scenarios and return comparison data |
| `GET` | `/api/graph` | LangGraph topology as JSON |
| `GET` | `/api/template/{type}` | Download sample data templates |

## File structure

```
core/
  state.py            -- SeeWeeSState TypedDict + create_state() + log_event()
  llm.py              -- Groq (qwen/qwen3-32b) primary, Claude fallback
  prompts.py          -- Prompt templates for DataAgent, AuditAgent, ReportAgent
  data_parser.py      -- CSV / Excel / JSON parsing with English + Chinese column aliases
  data_validator.py   -- Schema validation for uploaded files

agents/
  data_agent.py       -- Rule-based anomaly detection + LLM data quality assessment
  planner_agent.py    -- Route planning, cold-chain matching, cross-region driver borrowing
  audit_agent.py      -- Safety constraint checks + LLM correction guidance + audit_decision()
  report_agent.py     -- KPI calculation + LLM executive summary
  checkpoint_agent.py -- HumanCheckpoint with LangGraph interrupt()

simulator/
  data_generator.py   -- Synthetic data for 6 disruption scenarios

dashboard/
  index.html          -- Web UI (approval banner added for HITL)

system.py             -- StateGraph assembly, run_cycle(), run_cycle_hitl(), resume_cycle()
system_upload.py      -- Upload pipeline (validates, fills missing data, runs graph)
server.py             -- FastAPI server (6 endpoints)
run_demo.py           -- CLI entry point
requirements.txt      -- Python dependencies
.env.example          -- Environment variable template
```

## KPI definitions

| KPI | Formula | Thresholds |
|---|---|---|
| Delivery Delay Risk | `delayed(>30min) / total × 100` | Green < 5%, Amber < 12%, Red ≥ 12% |
| Service Level | `on-time(≤30min) / total × 100` | Target ≥ 95%, Watch ≥ 90%, Critical < 90% |
| Resource Utilization | `assigned routes / total routes × 100` | Optimal 70–90%, Underused < 70%, Strained > 90% |
| Correction Loops | Count of audit retry cycles | Normal ≤ 1, Warning = 2, Escalation = 3 |
| Data Quality | `valid fields / (records × fields) × 100` | Good ≥ 90%, Fair ≥ 70%, Poor < 70% |
| Composite Risk | Weighted: anomalies×12, critical violations×15, warnings×5, weather×10, data penalty+10 | Low < 30, Moderate < 65, High ≥ 65 |

## Data format

Deliveries are the only required input. Inventory, drivers, and weather are auto-filled with simulated defaults if not provided.

| Type | Required fields |
|---|---|
| Deliveries | `region, destination, medicine, priority` |
| Inventory | `warehouse, region, capacityUsed, operational` |
| Drivers | `id, region, status, hasColdChainVehicle` |
| Weather | `region, condition, riskLevel` |

Column headers can be in English or Chinese. The parser maps common Chinese equivalents automatically.
