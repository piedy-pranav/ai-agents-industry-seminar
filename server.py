"""
FastAPI server — same 5 API endpoints as the original Express server.
Serves the dashboard HTML as a static file at the root.

Start with:
    uvicorn server:app --port 3200 --reload
"""

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from system import describe_graph, run_cycle_hitl, resume_cycle, run_what_if
from system_upload import run_cycle_with_data

app = FastAPI(title="SeeWeeS Medical Logistics", docs_url=None, redoc_url=None)

PORT = int(os.getenv("PORT", 3200))
DASHBOARD_DIR = Path(__file__).parent / "dashboard"


# ── Request models ────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    scenario: str = "normal"
    deliveryCount: int = 25
    driverCount: int = 24


class ApproveRequest(BaseModel):
    threadId: str
    decision: str   # "approved" | "rejected"


# ── API routes (must be registered before the static-file catch-all) ──────────

@app.post("/api/run")
async def api_run(req: RunRequest):
    try:
        state, is_interrupted, thread_id = await asyncio.to_thread(
            run_cycle_hitl,
            scenario=req.scenario,
            delivery_count=req.deliveryCount,
            driver_count=req.driverCount,
        )
        return {
            "ok": True,
            "state": state,
            "awaitingApproval": is_interrupted,
            "threadId": thread_id if is_interrupted else None,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.post("/api/approve")
async def api_approve(req: ApproveRequest):
    try:
        if req.decision not in ("approved", "rejected"):
            return JSONResponse(status_code=400, content={"ok": False, "error": "decision must be 'approved' or 'rejected'"})
        state = await asyncio.to_thread(resume_cycle, req.threadId, req.decision)
        return {"ok": True, "state": state}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.post("/api/upload")
async def api_upload(files: list[UploadFile] = File(...)):
    if not files:
        return JSONResponse(status_code=400, content={"ok": False, "error": "No files uploaded"})
    try:
        raw = [{"filename": f.filename, "content": await f.read()} for f in files]
        state = await asyncio.to_thread(run_cycle_with_data, raw)
        return {"ok": True, "state": state}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.post("/api/whatif")
async def api_whatif():
    try:
        results = await asyncio.to_thread(run_what_if)
        return {"ok": True, "results": results}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.get("/api/graph")
async def api_graph():
    return describe_graph()


@app.get("/api/template/{data_type}")
async def api_template(data_type: str):
    templates = {
        "deliveries": [
            {"id": "DLV-0001", "region": "Region A", "origin": "Warehouse A", "destination": "Hospital A1",
             "medicine": "Insulin", "priority": "critical", "quantity": 200, "scheduledTime": "08:30",
             "estimatedDelay": 12, "requiresColdChain": True, "weight": 10.5},
            {"id": "DLV-0002", "region": "Region B", "origin": "Warehouse B", "destination": "Hospital B3",
             "medicine": "Vaccines", "priority": "high", "quantity": 150, "scheduledTime": "09:00",
             "estimatedDelay": 5, "requiresColdChain": True, "weight": 4.5},
            {"id": "DLV-0003", "region": "Region A", "origin": "Warehouse A", "destination": "Hospital A2",
             "medicine": "Antibiotics", "priority": "standard", "quantity": 500, "scheduledTime": "10:30",
             "estimatedDelay": 0, "requiresColdChain": False, "weight": 40.0},
        ],
        "inventory": [
            {"warehouse": "Warehouse A", "region": "Region A", "capacityUsed": 78, "totalCapacity": 100,
             "operational": True, "coldStorageAvailable": True},
            {"warehouse": "Warehouse B", "region": "Region B", "capacityUsed": 92, "totalCapacity": 100,
             "operational": True, "coldStorageAvailable": True},
            {"warehouse": "Warehouse C", "region": "Region C", "capacityUsed": 65, "totalCapacity": 100,
             "operational": True, "coldStorageAvailable": False},
        ],
        "drivers": [
            {"id": "DRV-001", "name": "Zhang Wei", "region": "Region A", "status": "available",
             "hoursWorked": 2, "maxHours": 12, "vehicleCapacity": 500, "hasColdChainVehicle": True},
            {"id": "DRV-002", "name": "Li Na", "region": "Region B", "status": "available",
             "hoursWorked": 0, "maxHours": 12, "vehicleCapacity": 800, "hasColdChainVehicle": False},
            {"id": "DRV-003", "name": "Wang Fang", "region": "Region A", "status": "on_route",
             "hoursWorked": 6, "maxHours": 12, "vehicleCapacity": 600, "hasColdChainVehicle": True},
        ],
        "weather": [
            {"region": "Region A", "condition": "clear", "temperature": 22, "windSpeed": 15,
             "visibility": "normal", "riskLevel": "low", "roadCondition": "normal"},
            {"region": "Region B", "condition": "heavy_rain", "temperature": 18, "windSpeed": 45,
             "visibility": "low", "riskLevel": "high", "roadCondition": "hazardous"},
            {"region": "Region C", "condition": "rain", "temperature": 20, "windSpeed": 25,
             "visibility": "normal", "riskLevel": "medium", "roadCondition": "normal"},
        ],
    }

    data = templates.get(data_type)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Unknown template type: {data_type}")

    from fastapi.responses import Response
    import json
    return Response(
        content=json.dumps(data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={data_type}_template.json"},
    )


# ── Dashboard static files (registered last so API routes take priority) ──────

if DASHBOARD_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
else:
    @app.get("/")
    async def no_dashboard():
        return {"message": "Dashboard not found. Copy dashboard/index.html into lngrph-implementation/dashboard/"}


# ── Dev entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print(f"\n  SeeWeeS Dashboard (LangGraph) → http://localhost:{PORT}\n")
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=True)
