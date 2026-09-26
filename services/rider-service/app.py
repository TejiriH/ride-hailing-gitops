"""rider-service: the people who request rides.

State is kept in memory, so it resets when the pod restarts and must run as a
single replica (two replicas would each have their own copy of the data).
"""
import os
import threading
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

SERVICE = "rider-service"
VERSION = os.getenv("APP_VERSION", "dev")

app = FastAPI(title=SERVICE)
lock = threading.Lock()

RIDERS = {
    "r1": {"id": "r1", "name": "Amara Okafor", "phone": "+44 7700 900001"},
    "r2": {"id": "r2", "name": "Tom Hughes", "phone": "+44 7700 900002"},
    "r3": {"id": "r3", "name": "Priya Shah", "phone": "+44 7700 900003"},
}


class NewRider(BaseModel):
    name: str
    phone: str


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/riders")
def list_riders():
    return list(RIDERS.values())


@app.get("/riders/{rider_id}")
def get_rider(rider_id: str):
    rider = RIDERS.get(rider_id)
    if rider is None:
        raise HTTPException(404, f"rider {rider_id} not found")
    return rider


@app.post("/riders", status_code=201)
def create_rider(body: NewRider):
    rider_id = "r" + uuid.uuid4().hex[:6]
    rider = {"id": rider_id, **body.model_dump()}
    with lock:
        RIDERS[rider_id] = rider
    return rider
