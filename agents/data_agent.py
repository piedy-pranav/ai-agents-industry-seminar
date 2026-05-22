"""
DataAgent — Phase 1 of the LangGraph pipeline.

Rule-based layer:
  - Fills missing weight fields using per-drug historical averages
  - Detects anomalies: delivery delays, warehouse issues, driver shortages, weather risks
  - Calculates a composite data quality score

LLM layer (Groq / Claude):
  - Interprets detected anomalies in operational context
  - Assesses planning confidence given the current data quality
  - Identifies the highest-priority data collection fix needed
"""

from core.state import SeeWeeSState, log_event
from core.data_validator import calculate_data_quality
from core.llm import call_llm
from core.prompts import DATA_QUALITY_SYSTEM, DATA_QUALITY_USER

ANOMALY_DELAY_THRESHOLD = 60   # minutes — delays beyond this are flagged
CAPACITY_WARNING = 90          # % warehouse utilisation

# Historical average kg per unit by drug type (used to fill missing weight)
_WEIGHT_PER_UNIT: dict[str, float] = {
    "Insulin": 0.05,
    "Vaccines": 0.03,
    "Antibiotics": 0.08,
    "Blood Products": 0.25,
    "Chemotherapy Drugs": 0.15,
}


def _estimate_weight(medicine: str, quantity: int | float) -> float:
    kg_per_unit = _WEIGHT_PER_UNIT.get(medicine, 0.1)
    return round(kg_per_unit * quantity, 2)


def data_agent(state: SeeWeeSState) -> SeeWeeSState:
    log_event(state, "DataAgent", "started")

    deliveries = state["data"]["deliveries"]
    inventory = state["data"]["inventory"]
    drivers = state["data"]["drivers"]
    weather = state["data"]["weather"]

    anomalies: list[dict] = []
    missing_fields: list[dict] = []
    augmented: list[dict] = []

    # ── 1. Fill missing weight fields ────────────────────────────────────────
    for i, d in enumerate(deliveries):
        if d.get("weight") is None:
            if i < 100:
                missing_fields.append({"record": d.get("id"), "field": "weight", "type": "delivery"})
            d["weight"] = _estimate_weight(d.get("medicine", ""), d.get("quantity", 0))
            d["_augmented"] = True
            if i < 100:
                augmented.append({"id": d.get("id"), "field": "weight", "method": "historical_average"})

    # ── 2. Data quality score ─────────────────────────────────────────────────
    quality_score = calculate_data_quality(deliveries)

    # ── 3. Anomaly detection ──────────────────────────────────────────────────

    # Delivery delays
    for d in deliveries:
        delay = d.get("estimatedDelay", 0) or 0
        if delay > ANOMALY_DELAY_THRESHOLD:
            anomalies.append({
                "type": "delivery_delay",
                "severity": "critical" if d.get("priority") == "critical" else "warning",
                "record": d.get("id"),
                "detail": (
                    f"{d.get('medicine')} to {d.get('destination')}: "
                    f"{delay}min delay (priority: {d.get('priority')})"
                ),
                "region": d.get("region"),
            })

    # Warehouse capacity
    for wh in inventory:
        if not wh.get("operational", True):
            anomalies.append({
                "type": "warehouse_offline",
                "severity": "critical",
                "record": wh.get("warehouse"),
                "detail": f"{wh.get('warehouse')} is non-operational",
                "region": wh.get("region"),
            })
        elif (wh.get("capacityUsed") or 0) >= CAPACITY_WARNING:
            cap = wh.get("capacityUsed", 0)
            anomalies.append({
                "type": "capacity_warning",
                "severity": "critical" if cap >= 95 else "warning",
                "record": wh.get("warehouse"),
                "detail": f"{wh.get('warehouse')} at {cap}% capacity",
                "region": wh.get("region"),
            })

    # Driver availability per region
    region_drivers: dict[str, dict] = {}
    for d in drivers:
        r = d.get("region", "Unknown")
        if r not in region_drivers:
            region_drivers[r] = {"total": 0, "available": 0}
        region_drivers[r]["total"] += 1
        if d.get("status") == "available":
            region_drivers[r]["available"] += 1

    for region, counts in region_drivers.items():
        avail_rate = counts["available"] / counts["total"] if counts["total"] else 0
        if avail_rate < 0.3:
            anomalies.append({
                "type": "driver_shortage",
                "severity": "critical",
                "record": region,
                "detail": (
                    f"{region}: only {counts['available']}/{counts['total']} drivers available "
                    f"({round(avail_rate * 100)}%)"
                ),
                "region": region,
            })

    # Weather risks
    for w in weather:
        if w.get("riskLevel") == "high":
            anomalies.append({
                "type": "weather_disruption",
                "severity": "critical",
                "record": w.get("region"),
                "detail": (
                    f"{w.get('region')}: {w.get('condition')}, "
                    f"wind {w.get('windSpeed')}km/h, visibility {w.get('visibility')}"
                ),
                "region": w.get("region"),
            })

    # ── 4. Write rule-based results to state ──────────────────────────────────
    state["data"]["anomalies"] = anomalies
    state["data"]["missingFields"] = missing_fields
    state["data"]["augmented"] = augmented
    state["data"]["dataQuality"] = quality_score

    # ── 5. LLM layer: interpret anomalies and assess planning confidence ──────
    llm_assessment = ""
    try:
        anomaly_lines = "\n".join(
            f"  - [{a['severity'].upper()}] {a['detail']}"
            for a in anomalies[:10]  # cap to avoid token bloat
        ) or "  None detected."

        llm_assessment = call_llm(
            system=DATA_QUALITY_SYSTEM,
            user=DATA_QUALITY_USER.format(
                quality_score=quality_score,
                total_deliveries=len(deliveries),
                augmented_count=len(augmented),
                anomaly_count=len(anomalies),
                anomalies_summary=anomaly_lines,
            ),
        )
    except Exception as e:
        print(f"[DataAgent] LLM call failed: {e}")

    state["data"]["llm_assessment"] = llm_assessment

    log_event(
        state, "DataAgent",
        f"completed: {len(anomalies)} anomalies, quality={quality_score}%"
        + (" | LLM assessment added" if llm_assessment else ""),
    )
    return state
