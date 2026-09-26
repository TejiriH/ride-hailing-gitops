"""gateway: the single entry point for the ride-hailing app.

- GET  /             a small web UI
- GET  /api/status   health + version of every backend service
- ANY  /api/<x>/...  proxied to the backend that owns <x>:
       riders -> rider-service, drivers -> driver-service,
       trips  -> trip-service,  quote   -> pricing-service
"""
import asyncio
import os

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

SERVICE = "gateway"
VERSION = os.getenv("APP_VERSION", "dev")

BACKENDS = {
    "rider-service": os.getenv("RIDER_SERVICE_URL", "http://rider-service"),
    "driver-service": os.getenv("DRIVER_SERVICE_URL", "http://driver-service"),
    "trip-service": os.getenv("TRIP_SERVICE_URL", "http://trip-service"),
    "pricing-service": os.getenv("PRICING_SERVICE_URL", "http://pricing-service"),
}
ROUTES = {
    "riders": "rider-service",
    "drivers": "driver-service",
    "trips": "trip-service",
    "quote": "pricing-service",
}

app = FastAPI(title=SERVICE)
client = httpx.AsyncClient(timeout=5.0)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/api/status")
async def status():
    async def check(name, url):
        try:
            r = await client.get(f"{url}/healthz", timeout=2.0)
            return {"service": name, "healthy": r.status_code == 200, "version": r.json().get("version")}
        except Exception as exc:
            return {"service": name, "healthy": False, "error": exc.__class__.__name__}

    results = await asyncio.gather(*(check(n, u) for n, u in BACKENDS.items()))
    services = [{"service": SERVICE, "healthy": True, "version": VERSION}, *results]
    return {"all_healthy": all(s["healthy"] for s in services), "services": services}


@app.api_route("/api/{prefix}{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(prefix: str, rest: str, request: Request):
    backend = ROUTES.get(prefix)
    if backend is None:
        raise HTTPException(404, f"no route for /api/{prefix}")
    url = f"{BACKENDS[backend]}/{prefix}{rest}"
    try:
        upstream = await client.request(
            request.method,
            url,
            params=request.query_params,
            content=await request.body(),
            headers={"content-type": request.headers.get("content-type", "application/json")},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"{backend} unreachable: {exc.__class__.__name__}")
    return Response(upstream.content, upstream.status_code, media_type=upstream.headers.get("content-type"))


@app.get("/", response_class=HTMLResponse)
async def ui():
    return INDEX_HTML.replace("{{VERSION}}", VERSION)


INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>RideHail</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#222}
 h1{margin-bottom:0} small{color:#777} table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}
 td,th{border-bottom:1px solid #eee;padding:.4rem;text-align:left;font-size:.9rem}
 .ok{color:#18794e}.bad{color:#c62828} button{padding:.4rem .8rem;cursor:pointer}
 select{padding:.35rem} #msg{margin:.5rem 0;min-height:1.2rem}
</style></head><body>
<h1>RideHail</h1><small>gateway {{VERSION}}</small>

<h2>Services</h2><table id="status"></table>

<h2>Request a ride</h2>
<select id="rider"></select> <button onclick="book()">Request ride</button>
<div id="msg"></div>

<h2>Trips</h2><table id="trips"></table>

<h2>Drivers</h2><table id="drivers"></table>

<script>
const $ = id => document.getElementById(id);
const j = (u, o) => fetch(u, o).then(async r => ({ok: r.ok, body: await r.json()}));
const rnd = (a, b) => a + Math.random() * (b - a);
const spot = () => ({lat: rnd(51.49, 51.53), lng: rnd(-0.15, -0.08)});   // somewhere in central London

async function refresh() {
  const s = (await j('/api/status')).body;
  $('status').innerHTML = '<tr><th>Service</th><th>Health</th><th>Version</th></tr>' +
    s.services.map(x => `<tr><td>${x.service}</td><td class="${x.healthy?'ok':'bad'}">${x.healthy?'healthy':'DOWN'}</td><td>${x.version||'-'}</td></tr>`).join('');
  const t = await j('/api/trips');
  $('trips').innerHTML = '<tr><th>Trip</th><th>Rider</th><th>Driver</th><th>Fare</th><th>Status</th><th></th></tr>' +
    (t.ok ? t.body : []).map(x => `<tr><td>${x.id}</td><td>${x.rider.name}</td><td>${x.driver.name} (${x.driver.car})</td>
      <td>${x.quote.fare} ${x.quote.currency}</td><td>${x.status}</td>
      <td>${x.status!=='completed'?`<button onclick="done('${x.id}')">Complete</button>`:''}</td></tr>`).join('');
  const d = await j('/api/drivers');
  $('drivers').innerHTML = '<tr><th>Driver</th><th>Car</th><th>Status</th></tr>' +
    (d.ok ? d.body : []).map(x => `<tr><td>${x.name}</td><td>${x.car} ${x.plate}</td><td>${x.status}</td></tr>`).join('');
}
async function loadRiders() {
  const r = await j('/api/riders');
  if (r.ok) $('rider').innerHTML = r.body.map(x => `<option value="${x.id}">${x.name}</option>`).join('');
}
async function book() {
  const r = await j('/api/trips', {method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({rider_id: $('rider').value, pickup: spot(), dropoff: spot()})});
  $('msg').textContent = r.ok ? `Booked ${r.body.id}: ${r.body.driver.name} is on the way, fare ${r.body.quote.fare} ${r.body.quote.currency}`
                              : `Failed: ${r.body.detail}`;
  refresh();
}
async function done(id) { await j(`/api/trips/${id}/complete`, {method: 'POST'}); refresh(); }
loadRiders(); refresh(); setInterval(refresh, 5000);
</script></body></html>"""
