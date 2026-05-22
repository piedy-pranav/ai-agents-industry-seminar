"""
Data parser — converts uploaded CSV / Excel / JSON files into the four
system data structures (deliveries, inventory, drivers, weather).

Supports flexible column names in English and Chinese. Logic is a direct
port of core/data-parser.mjs.
"""

import io
import json
import re
import pandas as pd

# ── Column alias maps (case-insensitive, punctuation-insensitive) ─────────────

DELIVERY_MAP: dict[str, list[str]] = {
    "id":                 ["id","delivery_id","deliveryid","order_id","orderid","编号","订单号","配送单号"],
    "region":             ["region","area","zone","district","区域","地区","片区"],
    "origin":             ["origin","warehouse","source","from","出发地","仓库","发货仓"],
    "destination":        ["destination","dest","to","hospital","target","目的地","收货地","医院"],
    "medicine":           ["medicine","drug","product","item","medication","药品","药物","产品","品名"],
    "priority":           ["priority","level","urgency","优先级","紧急程度"],
    "quantity":           ["quantity","qty","amount","count","数量"],
    "scheduledTime":      ["scheduled_time","scheduledtime","time","delivery_time","schedule","计划时间","配送时间","预计时间"],
    "estimatedDelay":     ["estimated_delay","estimateddelay","delay","delay_min","delay_minutes","延迟","预计延迟","延误分钟"],
    "requiresColdChain":  ["requires_cold_chain","requirescoldchain","cold_chain","coldchain","cold","冷链","需要冷链","是否冷链"],
    "weight":             ["weight","total_weight","kg","重量"],
    "status":             ["status","state","状态"],
}

INVENTORY_MAP: dict[str, list[str]] = {
    "warehouse":      ["warehouse","name","warehouse_name","仓库","仓库名"],
    "region":         ["region","area","zone","区域","地区"],
    "capacityUsed":   ["capacity_used","capacityused","used","usage","usage_pct","使用率","已用容量"],
    "totalCapacity":  ["total_capacity","totalcapacity","capacity","total","总容量"],
    "operational":    ["operational","active","status","open","运营中","是否运营"],
}

DRIVER_MAP: dict[str, list[str]] = {
    "id":                   ["id","driver_id","driverid","编号","司机编号"],
    "name":                 ["name","driver_name","drivername","姓名","司机"],
    "region":               ["region","area","zone","区域"],
    "status":               ["status","availability","state","状态"],
    "hoursWorked":          ["hours_worked","hoursworked","hours","worked_hours","已工作时长","工时"],
    "maxHours":             ["max_hours","maxhours","limit","最大工时","工时上限"],
    "vehicleCapacity":      ["vehicle_capacity","vehiclecapacity","capacity","载量","车辆容量"],
    "hasColdChainVehicle":  ["has_cold_chain","hascoldchain","cold_chain_vehicle","coldchain","冷链车","是否冷链车"],
}

WEATHER_MAP: dict[str, list[str]] = {
    "region":        ["region","area","zone","区域","地区"],
    "condition":     ["condition","weather","type","天气","天气状况"],
    "temperature":   ["temperature","temp","气温","温度"],
    "windSpeed":     ["wind_speed","windspeed","wind","风速"],
    "visibility":    ["visibility","vis","能见度"],
    "riskLevel":     ["risk_level","risklevel","risk","风险","风险等级"],
    "roadCondition": ["road_condition","roadcondition","road","路况"],
}


def _normalise(s: str) -> str:
    """Lowercase and strip spaces/underscores/hyphens for fuzzy column matching."""
    return re.sub(r"[\s_\-]", "", s.lower())


def _find_column(row: dict, aliases: list[str]) -> str | None:
    keys = list(row.keys())
    for alias in aliases:
        norm_alias = _normalise(alias)
        match = next((k for k in keys if _normalise(k) == norm_alias), None)
        if match is not None:
            return match
    return None


def _map_row(row: dict, mapping: dict, defaults: dict) -> dict:
    result = dict(defaults)
    for field, aliases in mapping.items():
        col = _find_column(row, aliases)
        if col is not None:
            val = row[col]
            if val is not None and val != "":
                result[field] = val
    return result


def _to_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val > 0
    if isinstance(val, str):
        return val.lower().strip() in ("true", "yes", "1", "是", "y", "有")
    return False


def _to_num(val, default=0):
    try:
        n = float(val)
        return default if (n != n) else n  # NaN check
    except (TypeError, ValueError):
        return default


# ── File parsing ──────────────────────────────────────────────────────────────

def _df_to_rows(df: pd.DataFrame) -> list[dict]:
    """Convert DataFrame to list of dicts, replacing NaN/NaT with None."""
    return [
        {k: (None if pd.isna(v) else v) for k, v in row.items()}
        for row in df.to_dict(orient="records")
    ]


def parse_file(content: bytes, filename: str) -> list[dict] | dict[str, list[dict]]:
    """
    Parse file bytes into row data.
    Returns a list[dict] for single-dataset files, or dict[sheetName, list[dict]]
    for multi-sheet Excel workbooks.
    """
    ext = filename.rsplit(".", 1)[-1].lower()

    if ext == "json":
        return json.loads(content.decode("utf-8"))

    if ext == "csv":
        df = pd.read_csv(io.BytesIO(content), dtype=str).where(pd.notna, None)
        return _df_to_rows(df)

    # Excel (.xlsx, .xls, .xlsm …)
    xl = pd.ExcelFile(io.BytesIO(content))
    sheets = {name: _df_to_rows(xl.parse(name).where(pd.notna, None)) for name in xl.sheet_names}
    if len(sheets) == 1:
        return next(iter(sheets.values()))
    return sheets


# ── Type detection ─────────────────────────────────────────────────────────────

def _detect_type(rows: list[dict]) -> str:
    if not rows:
        return "unknown"
    cols = [_normalise(k) for k in rows[0].keys()]

    delivery_hints = ["medicine","drug","hospital","coldchain","requirescoldchain","药品","药物","医院","冷链"]
    if any(h in c for h in delivery_hints for c in cols):
        return "deliveries"

    inventory_hints = ["warehouse","capacity","stocklevel","仓库","容量","库存"]
    if any(h in c for h in inventory_hints for c in cols):
        return "inventory"

    driver_hints = ["driverid","drivername","maxhours","vehiclecapacity","coldchainvehicle","司机","车辆"]
    if any(h in c for h in driver_hints for c in cols):
        return "drivers"

    weather_hints = ["weather","temperature","windspeed","roadcondition","天气","温度","风速"]
    if any(h in c for h in weather_hints for c in cols):
        return "weather"

    return "unknown"


# ── Main entry point ──────────────────────────────────────────────────────────

def process_uploaded_data(files: list[dict]) -> dict:
    """
    Parse and normalise uploaded files into system-compatible data structures.

    Args:
        files: list of {"filename": str, "content": bytes}

    Returns:
        {"data": {deliveries, inventory, drivers, weather}, "summary": {...}, "unrecognized": [...]}
    """
    data: dict[str, list] = {"deliveries": [], "inventory": [], "drivers": [], "weather": []}
    parsed_datasets: list[dict] = []  # list of {filename, rows}
    unrecognized: list[dict] = []

    # ── 1. Parse each file ────────────────────────────────────────────────────
    for file in files:
        try:
            content = parse_file(file["content"], file["filename"])
        except Exception as e:
            unrecognized.append({"filename": file["filename"], "error": str(e), "columns": [], "rowCount": 0})
            continue

        if isinstance(content, list):
            parsed_datasets.append({"filename": file["filename"], "rows": content})
        elif isinstance(content, dict):
            for sheet_name, rows in content.items():
                if isinstance(rows, list):
                    parsed_datasets.append({"filename": f"{file['filename']}/{sheet_name}", "rows": rows})

    # ── 2. Classify and map each dataset ─────────────────────────────────────
    for ds in parsed_datasets:
        filename = ds["filename"]
        rows = ds["rows"]
        if not rows:
            continue

        fn_lower = filename.lower()
        if "deliver" in fn_lower or "配送" in fn_lower:
            dtype = "deliveries"
        elif any(h in fn_lower for h in ("inventor", "warehouse", "仓库", "库存")):
            dtype = "inventory"
        elif any(h in fn_lower for h in ("driver", "司机", "车辆")):
            dtype = "drivers"
        elif any(h in fn_lower for h in ("weather", "天气")):
            dtype = "weather"
        else:
            dtype = _detect_type(rows)

        if dtype == "unknown":
            unrecognized.append({
                "filename": filename,
                "columns": list(rows[0].keys()) if rows else [],
                "rowCount": len(rows),
            })
            continue

        if dtype == "deliveries":
            mapped = []
            for i, row in enumerate(rows):
                d = _map_row(row, DELIVERY_MAP, {
                    "id": f"DLV-{i + 1:04d}",
                    "region": "Unknown",
                    "origin": "Unknown",
                    "destination": "Unknown",
                    "medicine": "Unknown",
                    "priority": "standard",
                    "quantity": 1,
                    "scheduledTime": "08:00",
                    "estimatedDelay": 0,
                    "requiresColdChain": False,
                    "weight": None,
                    "status": "pending",
                })
                d["quantity"] = int(_to_num(d["quantity"], 1))
                d["estimatedDelay"] = int(_to_num(d["estimatedDelay"], 0))
                d["requiresColdChain"] = _to_bool(d["requiresColdChain"])
                raw_w = d.get("weight")
                d["weight"] = float(_to_num(raw_w, 0)) if raw_w is not None else None
                mapped.append(d)
            data["deliveries"] = mapped

        elif dtype == "inventory":
            mapped = []
            for row in rows:
                inv = _map_row(row, INVENTORY_MAP, {
                    "warehouse": "Unknown",
                    "region": "Unknown",
                    "capacityUsed": 50,
                    "totalCapacity": 100,
                    "operational": True,
                })
                inv["capacityUsed"] = float(_to_num(inv["capacityUsed"], 50))
                inv["totalCapacity"] = float(_to_num(inv["totalCapacity"], 100))
                inv["operational"] = _to_bool(inv["operational"])
                inv.setdefault("stockLevels", {})
                inv.setdefault("coldStorageAvailable", True)
                mapped.append(inv)
            data["inventory"] = mapped

        elif dtype == "drivers":
            mapped = []
            for i, row in enumerate(rows):
                drv = _map_row(row, DRIVER_MAP, {
                    "id": f"DRV-{i + 1:03d}",
                    "name": f"Driver {i + 1}",
                    "region": "Unknown",
                    "status": "available",
                    "hoursWorked": 0,
                    "maxHours": 12,
                    "vehicleCapacity": 500,
                    "hasColdChainVehicle": False,
                })
                drv["hoursWorked"] = float(_to_num(drv["hoursWorked"], 0))
                drv["maxHours"] = float(_to_num(drv["maxHours"], 12))
                drv["vehicleCapacity"] = float(_to_num(drv["vehicleCapacity"], 500))
                drv["hasColdChainVehicle"] = _to_bool(drv["hasColdChainVehicle"])
                if drv["status"] not in ("available", "on_route", "resting", "unavailable"):
                    drv["status"] = "available"
                mapped.append(drv)
            data["drivers"] = mapped

        elif dtype == "weather":
            mapped = []
            for row in rows:
                w = _map_row(row, WEATHER_MAP, {
                    "region": "Unknown",
                    "condition": "clear",
                    "temperature": 20,
                    "windSpeed": 10,
                    "visibility": "normal",
                    "riskLevel": "low",
                    "roadCondition": "normal",
                })
                w["temperature"] = float(_to_num(w["temperature"], 20))
                w["windSpeed"] = float(_to_num(w["windSpeed"], 10))
                mapped.append(w)
            data["weather"] = mapped

    return {
        "data": data,
        "summary": {
            "deliveries": len(data["deliveries"]),
            "inventory": len(data["inventory"]),
            "drivers": len(data["drivers"]),
            "weather": len(data["weather"]),
        },
        "unrecognized": unrecognized,
    }
