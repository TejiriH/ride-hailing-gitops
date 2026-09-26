"""trip-service: books trips by orchestrating the other services.

Booking a trip:
  1. check the rider exists        -> rider-service
  2. get a fare for the distance   -> pricing-service
  3. grab the nearest free driver  -> driver-service

The other services are found by their Kubernetes Service names (plain DNS),
passed in through environment variables.
"""
import math
import os
import threading
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

SERVICE = "trip-service"
VERSION = os.getenv("APP_VERSION", "dev")
RIDER_URL = os.getenv("RIDER_SERVICE_URL", "http://rider-service")
DRIVER_URL = os.getenv("DRIVER_SERVICE_URL", "http://driver-service")
PRICING_URL = os.getenv("PRICING_SERVICE_URL", "http://pricing-service")

app = FastAPI(title=SERVICE)
client = httpx.Client(timeout=3.0)
lock = threading.Lock()
TRIPS: dict[str, dict] = {}


class Location(BaseModel):
    lat: float
    lng: float


class TripRequest(BaseModel):
    rider_id: str
    pickup: Location
    dropoff: Location


def haversine_km(a: Location, b: Location):
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = math.radians(b.lat - a.lat), math.radians(b.lng - a.lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def call(method: str, url: str, **kwargs):
    """Call a downstream service, turning network failures into a 502."""
    try:
        return client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not reach {url}: {exc.__class__.__name__}")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/trips")
def list_trips():
    return sorted(TRIPS.values(), key=lambda t: t["requested_at"], reverse=True)


@app.get("/trips/{trip_id}")
def get_trip(trip_id: str):
    trip = TRIPS.get(trip_id)
    if trip is None:
        raise HTTPException(404, f"trip {trip_id} not found")
    return trip


@app.post("/trips", status_code=201)
def request_trip(req: TripRequest):
    rider = call("GET", f"{RIDER_URL}/riders/{req.rider_id}")
    if rider.status_code == 404:
        raise HTTPException(400, f"unknown rider {req.rider_id}")
    rider.raise_for_status()

    distance = haversine_km(req.pickup, req.dropoff)
    quote = call("POST", f"{PRICING_URL}/quote", json={"distance_km": distance})
    quote.raise_for_status()

    driver = call("POST", f"{DRIVER_URL}/drivers/assign", json=req.pickup.model_dump())
    if driver.status_code == 409:
        raise HTTPException(503, "no drivers available, try again shortly")
    driver.raise_for_status()

    trip = {
        "id": "t" + uuid.uuid4().hex[:8],
        "status": "driver_assigned",
        "rider": rider.json(),
        "driver": driver.json(),
        "pickup": req.pickup.model_dump(),
        "dropoff": req.dropoff.model_dump(),
        "quote": quote.json(),
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    with lock:
        TRIPS[trip["id"]] = trip
    return trip


@app.post("/trips/{trip_id}/complete")
def complete_trip(trip_id: str):
    with lock:
        trip = TRIPS.get(trip_id)
        if trip is None:
            raise HTTPException(404, f"trip {trip_id} not found")
        if trip["status"] == "completed":
            return trip
        trip["status"] = "completed"
    call("POST", f"{DRIVER_URL}/drivers/{trip['driver']['id']}/release")
    return trip
