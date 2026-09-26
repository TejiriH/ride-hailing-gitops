"""driver-service: drivers, where they are, and whether they are free.

State is kept in memory, so it resets when the pod restarts and must run as a
single replica (two replicas could assign the same driver twice).
"""
import math
import os
import threading

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

SERVICE = "driver-service"
VERSION = os.getenv("APP_VERSION", "dev")

app = FastAPI(title=SERVICE)
lock = threading.Lock()

# Seed drivers scattered around central London.
DRIVERS = {
    "d1": {"id": "d1", "name": "Kwame Mensah", "car": "Toyota Prius", "plate": "LX21 ABC", "lat": 51.5074, "lng": -0.1278, "status": "available"},
    "d2": {"id": "d2", "name": "Sofia Rossi", "car": "Kia Niro", "plate": "LB70 XYZ", "lat": 51.5155, "lng": -0.0922, "status": "available"},
    "d3": {"id": "d3", "name": "Ben Carter", "car": "Tesla Model 3", "plate": "EV22 TES", "lat": 51.5033, "lng": -0.1195, "status": "available"},
    "d4": {"id": "d4", "name": "Chen Wei", "car": "Skoda Octavia", "plate": "LN19 OCT", "lat": 51.5290, "lng": -0.1255, "status": "available"},
    "d5": {"id": "d5", "name": "Fatima Bello", "car": "Hyundai Ioniq", "plate": "LV23 ION", "lat": 51.4975, "lng": -0.1357, "status": "offline"},
}


class Location(BaseModel):
    lat: float
    lng: float


class StatusUpdate(BaseModel):
    status: str  # available | offline


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/drivers")
def list_drivers(status: str | None = None):
    return [d for d in DRIVERS.values() if status is None or d["status"] == status]


@app.get("/drivers/{driver_id}")
def get_driver(driver_id: str):
    driver = DRIVERS.get(driver_id)
    if driver is None:
        raise HTTPException(404, f"driver {driver_id} not found")
    return driver


@app.post("/drivers/assign")
def assign_nearest(pickup: Location):
    """Pick the nearest available driver and mark them busy."""
    with lock:
        free = [d for d in DRIVERS.values() if d["status"] == "available"]
        if not free:
            raise HTTPException(409, "no drivers available")
        nearest = min(free, key=lambda d: haversine_km(d["lat"], d["lng"], pickup.lat, pickup.lng))
        nearest["status"] = "busy"
        eta_km = haversine_km(nearest["lat"], nearest["lng"], pickup.lat, pickup.lng)
    return {**nearest, "distance_to_pickup_km": round(eta_km, 2)}


@app.post("/drivers/{driver_id}/release")
def release(driver_id: str):
    with lock:
        driver = DRIVERS.get(driver_id)
        if driver is None:
            raise HTTPException(404, f"driver {driver_id} not found")
        driver["status"] = "available"
    return driver


@app.post("/drivers/{driver_id}/status")
def set_status(driver_id: str, body: StatusUpdate):
    if body.status not in ("available", "offline"):
        raise HTTPException(400, "status must be 'available' or 'offline'")
    with lock:
        driver = DRIVERS.get(driver_id)
        if driver is None:
            raise HTTPException(404, f"driver {driver_id} not found")
        driver["status"] = body.status
    return driver
