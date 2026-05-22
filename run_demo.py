"""
CLI entry point — runs a single named scenario and prints a summary.
Usage: python run_demo.py [scenario]

Scenarios: normal | demandSurge | warehouseClosure | driverShortage | severeWeather | compound
"""

import sys
from dotenv import load_dotenv
load_dotenv()

from system import run_cycle

VALID = ["normal", "demandSurge", "warehouseClosure", "driverShortage", "severeWeather", "compound"]

scenario = sys.argv[1] if len(sys.argv) > 1 else "normal"
if scenario not in VALID:
    print(f"Unknown scenario '{scenario}'. Choose from: {', '.join(VALID)}")
    sys.exit(1)

print(f"\n  SeeWeeS LangGraph — running scenario: {scenario}\n")

def on_step(info):
    print(f"  [{info['node']}] done")

state = run_cycle(scenario=scenario, on_step=on_step)

print(f"\n  ─── Results ───────────────────────────────")
print(f"  Cycle ID    : {state['cycleId']}")
print(f"  Status      : {state['flow']['status']}")
print(f"  Risk score  : {state['audit']['riskScore']}/100")
print(f"  Corrections : {state['audit']['correctionCount']}")
print(f"  Data quality: {state['data']['dataQuality']}%")
print(f"  Routes      : {len(state['plan']['routes'])}")
print(f"  Anomalies   : {len(state['data']['anomalies'])}")

kpis = state["report"].get("kpis", {})
if kpis:
    print(f"\n  ─── KPIs ──────────────────────────────────")
    for k, v in kpis.items():
        note = f"  [{v.get('note','').split('—')[0].strip()}]" if v.get("note") else ""
        print(f"  {v['label']:25s}: {v['value']}{v['unit']}  ({v['status']}){note}")

print(f"\n  ─── Executive Summary ─────────────────────")
print(f"  {state['report'].get('summary', '').replace(chr(10), chr(10) + '  ')}")

llm_note = state["data"].get("llm_assessment", "")
if llm_note:
    print(f"\n  ─── LLM Data Assessment ───────────────────")
    print(f"  {llm_note.replace(chr(10), chr(10) + '  ')}")

print()
