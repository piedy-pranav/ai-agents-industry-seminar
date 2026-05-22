"""
PlannerAgent — Phase 2 of the LangGraph pipeline.

Rule-based layer:
  - Groups deliveries by region; auto-splits regions with > 200 deliveries
  - Sorts by priority (critical → high → standard) then scheduled time
  - Smart driver assignment: cold-chain medicines go to cold-chain vehicles first
  - On revisions (audit loop), borrows drivers from adjacent regions
  - Builds route objects and contingency plans for weather / unassigned routes

No LLM call in the planner — the hard assignment constraints are deterministic
and correctness is more important than fluency here. The AuditAgent's LLM layer
provides specific correction steps that guide the next revision.
"""

import math
from core.state import SeeWeeSState, log_event

MAX_PER_REGION = 200
PRIORITY_ORDER = {"critical": 0, "high": 1, "standard": 2}


def _route_time(deliveries: list[dict], weather_hazard: bool) -> int:
    base = len(deliveries) * 18
    weather_penalty = base * 0.35 if weather_hazard else 0
    priority_bonus = -10 if any(d.get("priority") == "critical" for d in deliveries) else 0
    return round(base + weather_penalty + priority_bonus)


def planner_agent(state: SeeWeeSState) -> SeeWeeSState:
    revision = state["plan"]["revision"]
    log_event(state, "PlannerAgent", f"started (revision {revision})")

    deliveries = state["data"]["deliveries"]
    inventory = state["data"]["inventory"]
    drivers = state["data"]["drivers"]
    weather = state["data"]["weather"]
    prev_violations = state["audit"]["violations"]

    # ── 1. Group and optionally split oversized regions ───────────────────────
    region_groups: dict[str, list] = {}
    for d in deliveries:
        r = d.get("region", "Unknown")
        region_groups.setdefault(r, []).append(d)

    split_groups: dict[str, list] = {}
    for region, items in region_groups.items():
        if len(items) <= MAX_PER_REGION:
            split_groups[region] = items
        else:
            n_splits = math.ceil(len(items) / MAX_PER_REGION)
            for s in range(n_splits):
                sub = f"Zone-{chr(65 + s % 26)}" if region == "Unknown" else f"{region}-{s + 1}"
                split_groups[sub] = items[s * MAX_PER_REGION:(s + 1) * MAX_PER_REGION]
    region_groups = split_groups

    routes: list[dict] = []
    allocations: list[dict] = []
    route_idx = 0
    global_used_drivers: set[str] = set()

    for region, region_deliveries in region_groups.items():
        # Sort: priority first, then scheduled time
        sorted_deliveries = sorted(
            region_deliveries,
            key=lambda d: (
                PRIORITY_ORDER.get(d.get("priority", "standard"), 2),
                str(d.get("scheduledTime", "")),
            ),
        )

        # Available drivers for this region
        region_drivers = [
            d for d in drivers
            if d.get("region") == region
            and d.get("status") == "available"
            and d.get("id") not in global_used_drivers
        ]

        # On revision: borrow from other regions if short
        if revision > 0 and len(region_drivers) < math.ceil(len(sorted_deliveries) / 3):
            borrowed = [
                d for d in drivers
                if d.get("region") != region
                and d.get("status") == "available"
                and d.get("id") not in global_used_drivers
            ][:3]
            region_drivers = region_drivers + borrowed

        weather_entry = next((w for w in weather if w.get("region") == region), None)
        is_weather_hazard = (weather_entry or {}).get("riskLevel") == "high"

        # ── 2. Batch deliveries and assign drivers ────────────────────────────
        batch_size = max(3, math.ceil(len(sorted_deliveries) / max(len(region_drivers), 1)))
        batches = [
            sorted_deliveries[i:i + batch_size]
            for i in range(0, len(sorted_deliveries), batch_size)
        ]

        cold_batches = [b for b in batches if any(d.get("requiresColdChain") for d in b)]
        normal_batches = [b for b in batches if not any(d.get("requiresColdChain") for d in b)]
        cold_drivers = [d for d in region_drivers if d.get("hasColdChainVehicle")]
        normal_drivers = [d for d in region_drivers if not d.get("hasColdChainVehicle")]

        assigned_pairs: list[tuple] = []
        used_driver_ids: set[str] = set()

        # Cold-chain batches get cold-chain drivers first
        for i, batch in enumerate(cold_batches):
            driver = cold_drivers[i] if i < len(cold_drivers) else None
            if driver:
                used_driver_ids.add(driver["id"])
                global_used_drivers.add(driver["id"])
            assigned_pairs.append((batch, driver, True))

        remaining = [d for d in cold_drivers if d["id"] not in used_driver_ids] + normal_drivers
        for i, batch in enumerate(normal_batches):
            driver = remaining[i] if i < len(remaining) else None
            if driver:
                global_used_drivers.add(driver["id"])
            assigned_pairs.append((batch, driver, False))

        # ── 3. Build route objects ─────────────────────────────────────────────
        for batch, driver, _ in assigned_pairs:
            route_idx += 1
            has_cold = any(d.get("requiresColdChain") for d in batch)
            route_id = f"RT-{route_idx:03d}"

            routes.append({
                "id": route_id,
                "region": region,
                "deliveries": [d.get("id") for d in batch],
                "driver": driver["id"] if driver else "UNASSIGNED",
                "driverName": driver.get("name", "Unassigned") if driver else "Unassigned",
                "estimatedTime": _route_time(batch, is_weather_hazard),
                "deliveryCount": len(batch),
                "hasColdChain": has_cold,
                "driverHasColdChain": driver.get("hasColdChainVehicle", False) if driver else False,
                "weatherRisk": "high" if is_weather_hazard else "normal",
                "adjusted": revision > 0,
                "contingencyRoute": f"{route_id}-B" if is_weather_hazard else None,
                "totalWeight": round(sum(d.get("weight", 0) for d in batch), 2),
                "maxPriority": batch[0].get("priority", "standard") if batch else "standard",
            })

        wh = next((w for w in inventory if w.get("region") == region), None)
        allocations.append({
            "region": region,
            "driversAssigned": len(region_drivers),
            "deliveriesAssigned": len(region_deliveries),
            "warehouse": wh.get("warehouse", "Unknown") if wh else "Unknown",
            "capacityUsed": wh.get("capacityUsed", 0) if wh else 0,
        })

    # ── 4. Contingencies ───────────────────────────────────────────────────────
    contingencies = [
        {
            "originalRoute": r["id"],
            "issue": "no_driver" if r["driver"] == "UNASSIGNED" else "weather_risk",
            "fallback": r["contingencyRoute"] or f"{r['id']}-FALLBACK",
            "action": (
                "Request driver from adjacent region or delay standard-priority deliveries"
                if r["driver"] == "UNASSIGNED"
                else f"Activate alternate route {r['contingencyRoute']}, add 20min buffer"
            ),
        }
        for r in routes
        if r["weatherRisk"] == "high" or r["driver"] == "UNASSIGNED"
    ]

    # ── 5. Write to state ──────────────────────────────────────────────────────
    state["plan"]["routes"] = routes
    state["plan"]["allocations"] = allocations
    state["plan"]["contingencies"] = contingencies
    if revision > 0 and prev_violations:
        state["plan"]["revisionReasons"].extend(v["detail"] for v in prev_violations)

    unassigned = sum(1 for r in routes if r["driver"] == "UNASSIGNED")
    cold_mismatches = sum(
        1 for r in routes
        if r["hasColdChain"] and not r["driverHasColdChain"] and r["driver"] != "UNASSIGNED"
    )
    log_event(
        state, "PlannerAgent",
        f"completed: {len(routes)} routes, {len(contingencies)} contingencies, "
        f"{unassigned} unassigned, {cold_mismatches} cold-chain mismatches",
    )
    return state
