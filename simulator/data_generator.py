"""
Synthetic data generator for 6 operational scenarios.
Direct port of simulator/data-generator.mjs — same distributions, same field names,
same scenario configs so the dashboard renders identically.
"""

import random
import time

REGIONS = ["Region A", "Region B", "Region C", "Region D"]
WAREHOUSES = ["Warehouse A", "Warehouse B", "Warehouse C", "Warehouse D", "Warehouse E"]
MEDICINE_TYPES = ["Insulin", "Vaccines", "Antibiotics", "Blood Products", "Chemotherapy Drugs"]
PRIORITIES = ["critical", "high", "standard"]
WEATHER_CONDITIONS = ["clear", "rain", "heavy_rain", "storm", "snow", "fog"]


def _uid() -> str:
    return f"DLV-{int(time.time() * 1000):x}-{random.randint(1000, 9999)}"


def _round2(n: float) -> float:
    return round(n, 2)


def generate_deliveries(count: int = 25, scenario: dict | None = None) -> list[dict]:
    scenario = scenario or {}
    surge = scenario.get("demandSurge", {})

    def demand_multiplier(region: str) -> float:
        if surge.get("region") == region:
            return float(surge.get("factor", 1))
        return 1.0

    deliveries = []
    for _ in range(count):
        region = random.choice(REGIONS)
        adjusted_count = max(1, round(demand_multiplier(region)))

        for _ in range(adjusted_count):
            priority = random.choice(PRIORITIES)
            scheduled_hour = random.randint(6, 22)
            scheduled_min = random.randint(0, 59)

            if priority == "critical":
                delay = random.randint(0, 15)
            elif priority == "high":
                delay = random.randint(0, 45)
            else:
                delay = random.randint(0, 90)

            has_missing_data = random.random() < 0.08

            deliveries.append({
                "id": _uid(),
                "region": region,
                "origin": random.choice(WAREHOUSES),
                "destination": f"{region} Hospital {random.randint(1, 5)}",
                "medicine": random.choice(MEDICINE_TYPES),
                "priority": priority,
                "quantity": random.randint(10, 500),
                "scheduledTime": f"{scheduled_hour:02d}:{scheduled_min:02d}",
                "estimatedDelay": delay,
                "requiresColdChain": random.random() < 0.4,
                "weight": None if has_missing_data else _round2(random.uniform(2, 50)),
                "status": "pending",
            })

    return deliveries


def generate_inventory(scenario: dict | None = None) -> list[dict]:
    scenario = scenario or {}
    closed = (scenario.get("warehouseClosure") or {}).get("warehouse")

    result = []
    for i, wh in enumerate(WAREHOUSES):
        is_closed = wh == closed
        result.append({
            "warehouse": wh,
            "region": REGIONS[i % len(REGIONS)],
            "capacityUsed": 100 if is_closed else random.randint(55, 96),
            "totalCapacity": 100,
            "stockLevels": {
                med: random.randint(0 if is_closed else 20, 500)
                for med in MEDICINE_TYPES
            },
            "operational": not is_closed,
            "coldStorageAvailable": False if is_closed else (random.random() > 0.1),
        })
    return result


def generate_drivers(count: int = 24, scenario: dict | None = None) -> list[dict]:
    scenario = scenario or {}
    shortage_pct = (scenario.get("driverShortage") or {}).get("reductionPercent", 0)
    effective_count = max(1, round(count * (1 - shortage_pct / 100)))

    drivers = []
    for i in range(count):
        available = i < effective_count
        status = random.choice(["available", "available", "on_route"]) if available else "unavailable"
        drivers.append({
            "id": f"DRV-{i + 1:03d}",
            "name": f"Driver {i + 1}",
            "region": REGIONS[i % len(REGIONS)],
            "status": status,
            "hoursWorked": random.randint(0, 10),
            "maxHours": 12,
            "vehicleCapacity": random.randint(200, 1000),
            "hasColdChainVehicle": random.random() < 0.3,
        })
    return drivers


def generate_weather(scenario: dict | None = None) -> list[dict]:
    scenario = scenario or {}
    disrupted_regions = set((scenario.get("severeWeather") or {}).get("regions", []))

    result = []
    for region in REGIONS:
        is_disrupted = region in disrupted_regions
        if is_disrupted:
            condition = random.choice(["storm", "heavy_rain", "snow"])
        else:
            condition = random.choice(["clear", "clear", "clear", "rain", "fog"])

        result.append({
            "region": region,
            "condition": condition,
            "temperature": random.randint(-5, 35),
            "windSpeed": random.randint(60, 120) if condition == "storm" else random.randint(5, 40),
            "visibility": "low" if condition in ("storm", "heavy_rain", "fog") else "normal",
            "riskLevel": "high" if is_disrupted else ("medium" if condition == "rain" else "low"),
            "roadCondition": "hazardous" if is_disrupted else "normal",
        })
    return result


SCENARIOS: dict[str, dict] = {
    "normal": {},
    "demandSurge": {
        "demandSurge": {"region": "Region A", "factor": 1.4},
    },
    "warehouseClosure": {
        "warehouseClosure": {"warehouse": "Warehouse D"},
    },
    "driverShortage": {
        "driverShortage": {"reductionPercent": 30},
    },
    "severeWeather": {
        "severeWeather": {"regions": ["Region B", "Region C"]},
    },
    "compound": {
        "demandSurge": {"region": "Region A", "factor": 1.3},
        "driverShortage": {"reductionPercent": 20},
        "severeWeather": {"regions": ["Region C"]},
    },
}


def generate_scenario(name: str) -> dict:
    return SCENARIOS.get(name, SCENARIOS["normal"])
