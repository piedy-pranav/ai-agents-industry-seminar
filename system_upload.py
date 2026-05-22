"""
Upload pipeline — validates uploaded files, fills missing data types with
simulated defaults, then runs the full LangGraph graph.
Port of system-upload.mjs.
"""

import time
from datetime import datetime, timezone

from core.state import create_state, log_event
from core.data_parser import process_uploaded_data
from core.data_validator import validate_uploaded_data
from simulator.data_generator import generate_inventory, generate_drivers, generate_weather
from system import build_graph


def run_cycle_with_data(files: list[dict]) -> dict:
    """
    Args:
        files: list of {"filename": str, "content": bytes}

    Returns the final SeeWeeSState dict.
    """
    # ── 1. Parse uploaded files ───────────────────────────────────────────────
    result = process_uploaded_data(files)
    parsed = result["data"]
    summary = result["summary"]
    unrecognized = result["unrecognized"]

    # ── 2. Validate ───────────────────────────────────────────────────────────
    # data_validator expects {"filename": str, "content": str} for keyword scan
    files_for_validator = [
        {"filename": f["filename"], "content": f["content"].decode("utf-8", errors="ignore")}
        for f in files
    ]
    validation = validate_uploaded_data(parsed, files_for_validator)

    # ── 3. Create fresh state ─────────────────────────────────────────────────
    state = create_state()
    state["cycleId"] = f"CYC-UP-{int(time.time() * 1000):X}"
    state["timestamp"] = datetime.now(timezone.utc).isoformat()

    # ── 4. Hard rejection on critical validation failures ─────────────────────
    if not validation["valid"]:
        log_event(state, "System", f"Upload rejected: {validation['summary']}")
        state["flow"]["status"] = "rejected"

        issue_lines = "\n".join(
            f"• [{i['severity'].upper()}] {i['message']}" for i in validation["issues"]
        )
        warn_lines = (
            "\nWarnings:\n" + "\n".join(f"• {w['message']}" for w in validation["warnings"])
            if validation["warnings"] else ""
        )
        unrecog_lines = (
            "\nUnrecognized files (skipped):\n"
            + "\n".join(
                f"• {u['filename']} ({u.get('rowCount', 0)} rows, "
                f"columns: {', '.join(str(c) for c in u.get('columns', [])[:5])}"
                + ("…" if len(u.get("columns", [])) > 5 else "") + ")"
                for u in unrecognized
            )
            if unrecognized else ""
        )

        state["report"] = {
            "kpis": {},
            "topRisks": [],
            "recommendations": [],
            "auditTrail": state["flow"]["history"],
            "summary": (
                f"DATA VALIDATION FAILED\n\n{issue_lines}{warn_lines}{unrecog_lines}\n\n"
                "Please upload data with medical logistics fields "
                "(region, destination, medicine, priority, etc.).\n"
                "Download templates from the dashboard for reference."
            ),
            "status": "REJECTED",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "uploadSummary": {
                **summary,
                "simulated": [],
                "filesReceived": [f["filename"] for f in files],
                "unrecognized": [u["filename"] for u in unrecognized],
                "validation": validation,
            },
        }
        return state

    # ── 5. Fill missing data types with simulated defaults ────────────────────
    state["data"]["deliveries"] = parsed["deliveries"]

    simulated: list[str] = []

    if parsed["inventory"]:
        state["data"]["inventory"] = parsed["inventory"]
    else:
        state["data"]["inventory"] = generate_inventory({})
        simulated.append("inventory (simulated)")

    if parsed["drivers"]:
        state["data"]["drivers"] = parsed["drivers"]
    else:
        driver_count = max(24, len(parsed["deliveries"]) // 10)
        state["data"]["drivers"] = generate_drivers(driver_count, {})
        simulated.append("drivers (simulated)")

    if parsed["weather"]:
        state["data"]["weather"] = parsed["weather"]
    else:
        state["data"]["weather"] = generate_weather({})
        simulated.append("weather (simulated)")

    log_event(
        state, "System",
        f"Upload cycle started: {summary['deliveries']} deliveries, "
        f"{summary['inventory']} warehouses, {summary['drivers']} drivers, "
        f"{summary['weather']} weather records"
        + (f". Auto-filled: {', '.join(simulated)}" if simulated else "")
        + (f". Skipped: {', '.join(u['filename'] for u in unrecognized)}" if unrecognized else "")
        + (f". Warnings: {len(validation['warnings'])}" if validation["warnings"] else ""),
    )

    # ── 6. Run the LangGraph graph ────────────────────────────────────────────
    state["flow"]["status"] = "running"
    graph = build_graph()
    state = graph.invoke(state)

    if state["flow"]["status"] == "running":
        state["flow"]["status"] = "completed"

    log_event(state, "System", f"Upload cycle completed: status={state['flow']['status']}")

    # ── 7. Attach upload metadata to report ───────────────────────────────────
    state["report"]["uploadSummary"] = {
        **summary,
        "simulated": simulated,
        "filesReceived": [f["filename"] for f in files],
        "unrecognized": [u["filename"] for u in unrecognized],
        "validation": validation,
    }

    return state
