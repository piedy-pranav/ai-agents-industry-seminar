# SeeWeeS — LangGraph Multi-Agent System

A multi-agent medical logistics dispatch system built on **LangGraph** with **Groq (qwen/qwen3-32b)** as the AI reasoning engine. The system processes simulated delivery, inventory, driver, and weather data through a pipeline of specialized agents, self-corrects dispatch plans that violate safety constraints, and pauses for human approval when operational risk is too high.

---

## Quickstart (5 steps)

> **Requirements:** Python 3.11+ and a free Groq API key ([console.groq.com](https://console.groq.com))

**1. Clone the repository**
```bash
git clone https://github.com/piedy-pranav/ai-agents-industry-seminar.git
cd ai-agents-industry-seminar
```

**2. Create a virtual environment and install dependencies**
```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**3. Add your API key**
```bash
cp .env.example .env
```
Open `.env` and replace `your_groq_api_key_here` with your key from [console.groq.com](https://console.groq.com). The system works without a key (LLM calls are skipped gracefully) but you will not see AI-generated summaries.

**4. Start the server**
```bash
uvicorn server:app --port 3200 --reload
```

**5. Open the dashboard**

Go to **http://localhost:3200** in your browser.

---

## Using the Dashboard

### Simulate a scenario
1. Select a scenario from the dropdown (e.g. **Severe Weather** or **Compound Crisis**)
2. Click **Run**
3. The four agents run in sequence — results appear with KPI cards, risk table, route breakdown, and a full audit trail

> **Try "Severe Weather" or "Compound Crisis"** — these produce a risk score ≥ 65 which triggers the **Human-in-the-Loop approval banner**. You will see Approve / Reject buttons appear above the results. This is the LangGraph `interrupt()` checkpoint in action.

### Compare all scenarios
Click **Compare All** to run all six disruption scenarios simultaneously and view a side-by-side bar chart of risk scores, service levels, and correction loops.

### Upload your own data
Switch to the **Upload** tab and drag in a CSV, Excel, or JSON file with delivery data. Only deliveries are required — inventory, driver, and weather data are auto-filled with realistic defaults if not provided.

Download sample templates from the dashboard to see the expected column format.

### Run from the command line (no browser needed)
```bash
python run_demo.py normal
python run_demo.py demandSurge
python run_demo.py warehouseClosure
python run_demo.py driverShortage
python run_demo.py severeWeather
python run_demo.py compound
```
Each run prints KPI results, the LLM data quality assessment, and the executive summary to the terminal.

---

## For Graders — Key LangGraph Files

The LangGraph implementation is self-contained in this folder. Here is where to find each graded component:

| What to look for | File |
|---|---|
| **StateGraph definition** — nodes, edges, conditional edge, `MemorySaver` | [`system.py`](system.py) |
| **Shared state** — `SeeWeeSState` TypedDict with sub-states for data, plan, audit, report, flow | [`core/state.py`](core/state.py) |
| **Conditional edge / audit loop** — `audit_decision()` routing function, retry vs. escalate logic | [`agents/audit_agent.py`](agents/audit_agent.py) |
| **`interrupt()` checkpoint** — genuine LangGraph pause for human approval | [`agents/checkpoint_agent.py`](agents/checkpoint_agent.py) |
| **LLM integration** — Groq primary, Claude fallback, `<think>` tag stripping | [`core/llm.py`](core/llm.py) |
| **Prompt templates** — data quality, audit correction, executive summary | [`core/prompts.py`](core/prompts.py) |
| **API server** — FastAPI with `/api/run`, `/api/approve`, `/api/upload`, `/api/whatif` | [`server.py`](server.py) |

---

## Enhancements Implemented

### 1. Self-Correction & Quality Assurance (Audit Loop)
`AuditAgent` checks five safety constraints after every planner run:
- Cold-chain compliance (medicine matched to cold-chain vehicle)
- Driver hour limits (max 12h per cycle)
- Route time limits (max 180 min per route)
- Unassigned critical deliveries
- Warehouse capacity overflow (> 95%)

If critical violations are found, a **conditional edge** routes back to `PlannerAgent` for a revision (up to 3×). On each retry, the planner borrows drivers from adjacent regions and adjusts assignments. After 3 failed retries, the plan is escalated.

The `AuditAgent` also calls the LLM to generate **numbered, route-specific correction steps** that appear in the audit trail and guide each revision.

### 2. What-if Scenario Simulation
Six disruption scenarios stress-test the system against realistic operational failures. All six can be compared side-by-side in the dashboard's bar chart view.

| Scenario | What changes |
|---|---|
| Normal | Baseline — no disruptions |
| Demand Surge | Region A delivery volume × 1.4 |
| Warehouse Closure | Warehouse D goes offline |
| Driver Shortage | 30% of drivers become unavailable |
| Severe Weather | Regions B and C hit with storm / snow conditions |
| Compound Crisis | Demand surge + driver shortage + severe weather simultaneously |

### 3. Human-in-the-Loop Checkpoint
When composite risk ≥ 65/100, the `HumanCheckpoint` node calls LangGraph's real `interrupt()`. Execution **genuinely pauses** — state is saved to a `MemorySaver` checkpointer. The dashboard shows the complete report with Approve / Reject buttons. When the manager decides, the server calls `Command(resume=decision)` to resume the graph from the checkpoint and commit the final status.

---

## Agent Summary

| Agent | What it does | Uses LLM? |
|---|---|---|
| **DataAgent** | Fills missing weight data, detects delays / warehouse issues / driver shortages / weather risks, scores data quality | Yes — explains anomalies, rates planning confidence |
| **PlannerAgent** | Groups deliveries by region, assigns cold-chain vehicles first, splits oversized regions, borrows drivers on retries | No |
| **AuditAgent** | Enforces 5 safety rules, calculates risk score, triggers retry or escalation | Yes — generates correction steps for the next revision |
| **ReportAgent** | Calculates 6 KPIs, ranks top risks, assembles recommendations | Yes — writes the C-suite executive summary |
| **HumanCheckpoint** | Pauses graph with `interrupt()` when risk ≥ 65, resumes after manager decision | No |

---

## KPI Definitions

| KPI | Formula | Status thresholds |
|---|---|---|
| Delivery Delay Risk | `deliveries with delay > 30 min / total × 100` | Green < 5% · Amber < 12% · Red ≥ 12% |
| Service Level | `on-time deliveries / total × 100` | Target ≥ 95% · Watch ≥ 90% · Critical < 90% |
| Resource Utilization | `assigned routes / total routes × 100` | Optimal 70–90% · Underused < 70% · Strained > 90% |
| Correction Loops | Count of audit retry cycles | Normal ≤ 1 · Warning = 2 · Escalation = 3 |
| Data Quality | `valid fields / (records × all fields) × 100` | Good ≥ 90% · Fair ≥ 70% · Poor < 70% |
| Composite Risk | Weighted sum: anomalies, violations, weather, data gaps | Low < 30 · Moderate < 65 · High ≥ 65 |

---

## Project Structure

```
core/
  state.py            — SeeWeeSState TypedDict shared across all LangGraph nodes
  llm.py              — LLM wrapper: Groq primary, Claude fallback
  prompts.py          — Prompt templates for the three LLM-calling agents
  data_parser.py      — Parses uploaded CSV / Excel / JSON files
  data_validator.py   — Validates uploaded data against the medical logistics schema

agents/
  data_agent.py       — Anomaly detection + LLM data quality assessment
  planner_agent.py    — Route planning and resource allocation
  audit_agent.py      — Safety constraint checks + LLM correction guidance
  report_agent.py     — KPI calculation + LLM executive summary
  checkpoint_agent.py — Human-in-the-loop node using LangGraph interrupt()

simulator/
  data_generator.py   — Generates synthetic data for 6 disruption scenarios

dashboard/
  index.html          — Web UI served by FastAPI

system.py             — LangGraph StateGraph: nodes, edges, run_cycle(), run_cycle_hitl(), resume_cycle()
system_upload.py      — Upload flow: parse → validate → fill defaults → run graph
server.py             — FastAPI server with 6 API endpoints
run_demo.py           — CLI entry point for terminal-based testing
requirements.txt      — All Python dependencies
.env.example          — Environment variable template (copy to .env and add your keys)
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | Recommended | Primary LLM. Free tier at [console.groq.com](https://console.groq.com) |
| `ANTHROPIC_API_KEY` | Optional | Fallback LLM if Groq hits a rate limit |
| `PORT` | Optional | Server port (default: 3200) |
