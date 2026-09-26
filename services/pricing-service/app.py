"""pricing-service: works out the fare for a trip.

Stateless, so it is safe to run many replicas. All pricing knobs come from
environment variables, which Kubernetes fills from the pricing-config ConfigMap.
"""
import os

from fastapi import FastAPI
from pydantic import BaseModel, Field

SERVICE = "pricing-service"
VERSION = os.getenv("APP_VERSION", "dev")
BASE_FARE = float(os.getenv("BASE_FARE", "2.50"))
PER_KM = float(os.getenv("PER_KM", "1.20"))
SURGE_MULTIPLIER = float(os.getenv("SURGE_MULTIPLIER", "1.0"))
CURRENCY = os.getenv("CURRENCY", "GBP")

app = FastAPI(title=SERVICE)


class QuoteRequest(BaseModel):
    distance_km: float = Field(ge=0)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.post("/quote")
def quote(req: QuoteRequest):
    fare = (BASE_FARE + PER_KM * req.distance_km) * SURGE_MULTIPLIER
    return {
        "distance_km": round(req.distance_km, 2),
        "fare": round(fare, 2),
        "currency": CURRENCY,
        "surge_multiplier": SURGE_MULTIPLIER,
        "priced_by": f"{SERVICE}@{VERSION}",
    }
