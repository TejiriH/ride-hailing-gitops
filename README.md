# RideHail: a 5-service app for practising Kubernetes and Argo CD

A small ride-hailing backend split into five microservices. It is deliberately
simple (in-memory data, no database) so that you can spend your time on the
deployment side: Kubernetes manifests, GitOps and Argo CD.

## Architecture

```
                 ┌─────────────┐
   browser ────▶ │   gateway   │  web UI + /api/* reverse proxy
                 └──────┬──────┘
        ┌───────────────┼────────────────┬──────────────────┐
        ▼               ▼                ▼                  ▼
 ┌─────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────────────────┐
 │rider-service│ │driver-service│ │ trip-service │ │ pricing-service │
 └─────────────┘ └──────────────┘ └──────┬───────┘ └─────────────────┘
        ▲               ▲                │                  ▲
        └───────────────┴────────────────┴──────────────────┘
                trip-service calls rider, pricing and driver
                         to book a trip
```

Booking a trip (`POST /api/trips`) touches four services: gateway → trip-service,
which checks the rider (rider-service), prices the distance (pricing-service) and
assigns the nearest free driver (driver-service).

## The services

All services listen on **port 8000** and have a health endpoint at **`GET /healthz`**,
which returns `{"status":"ok","service":...,"version":...}`.

| Service | What it does | Main endpoints | Stateful? |
|---|---|---|---|
| `gateway` | Web UI and single entry point. Proxies `/api/<x>/...` to the owning service | `GET /`, `GET /api/status`, `/api/riders/*`, `/api/drivers/*`, `/api/trips/*`, `/api/quote` | No |
| `rider-service` | Riders | `GET /riders`, `GET /riders/{id}`, `POST /riders` | **Yes** (in memory) |
| `driver-service` | Drivers, location, availability | `GET /drivers[?status=]`, `POST /drivers/assign`, `POST /drivers/{id}/release`, `POST /drivers/{id}/status` | **Yes** (in memory) |
| `trip-service` | Books and completes trips (orchestrates the others) | `GET /trips`, `POST /trips`, `POST /trips/{id}/complete` | **Yes** (in memory) |
| `pricing-service` | Fare calculation | `POST /quote` `{"distance_km": 5}` | No |

`GET /api/status` on the gateway checks every backend and reports each one's health
and version. It is handy for smoke tests and for seeing which version is live after
a rollout.

### Configuration (environment variables)

| Variable | Used by | Default | Notes |
|---|---|---|---|
| `RIDER_SERVICE_URL` | gateway, trip-service | `http://rider-service` | |
| `DRIVER_SERVICE_URL` | gateway, trip-service | `http://driver-service` | |
| `TRIP_SERVICE_URL` | gateway | `http://trip-service` | |
| `PRICING_SERVICE_URL` | gateway, trip-service | `http://pricing-service` | |
| `BASE_FARE` | pricing-service | `2.50` | |
| `PER_KM` | pricing-service | `1.20` | |
| `SURGE_MULTIPLIER` | pricing-service | `1.0` | A good value to change through Git and watch roll out |
| `CURRENCY` | pricing-service | `GBP` | |
| `APP_VERSION` | all | set at build time | Baked into the image with `--build-arg`; shown in the UI and `/api/status` |

The URL defaults have no port because they assume Kubernetes Services named
`rider-service`, `driver-service`, `trip-service` and `pricing-service` that
listen on port **80** and forward to the container's port **8000**. If you
name your Services exactly that, the gateway and trip-service need no env vars.
If you choose other names or ports, set the variables instead.

## Build the images

Each service has its own `Dockerfile`. From the `services/` directory:

```bash
VERSION=1.0.0
for s in gateway rider-service driver-service trip-service pricing-service; do
  docker build --build-arg APP_VERSION=$VERSION -t ridehail/$s:$VERSION $s
done
```

All five use the same `requirements.txt`, so Docker builds the dependency layer
once and every image reuses it. The first build is slow; the rest are almost instant.

Use a real version tag, not `latest`. Argo CD only acts on changes in Git. If
the manifest always says `:latest`, pushing a new image changes nothing in Git,
so Argo CD sees nothing to deploy. With a new tag, the change to the manifest is
what triggers the rollout.

## Run it locally with Docker (quick sanity check)

```bash
docker network create ridehail
E="-e RIDER_SERVICE_URL=http://rider-service:8000 -e DRIVER_SERVICE_URL=http://driver-service:8000 \
   -e TRIP_SERVICE_URL=http://trip-service:8000 -e PRICING_SERVICE_URL=http://pricing-service:8000"
for s in rider-service driver-service trip-service pricing-service; do
  docker run -d --rm --name $s --network ridehail $E ridehail/$s:1.0.0
done
docker run -d --rm --name gateway --network ridehail -p 8080:8000 $E ridehail/gateway:1.0.0
# open http://localhost:8080

# clean up
docker stop gateway rider-service driver-service trip-service pricing-service && docker network rm ridehail
```

(Docker has no Services mapping port 80 to 8000, which is why the URLs include `:8000` here.)

## Deploying to Kubernetes: what you need to know

This repo has no manifests on purpose; writing them is the exercise. Here is
what the app expects from you.

**Per service you'll want:** a Deployment and a ClusterIP Service (port 80 → targetPort 8000).

**Probes:** use `GET /healthz` on port 8000 for readiness and liveness. The app starts in about 1 to 2 seconds.

**Replicas:**
- `gateway` and `pricing-service` are stateless. Scale them as much as you like.
- `rider-service`, `driver-service` and `trip-service` keep data in memory. Keep them at **1 replica**.
  With 2 replicas, each pod holds its own copy of the data, and requests land on either
  pod at random. You'd see trips "disappear" and the same driver booked twice.
  Their data also resets whenever the pod restarts; that is expected here.

**Security context:** the images run as UID `10001` (non-root), so `runAsNonRoot: true` works.

**Resources:** each pod idles at about 40 to 50 Mi of memory. `requests: 50m CPU / 64Mi` and
`limits: 128Mi` are plenty.

**Config:** the pricing variables are a natural fit for a ConfigMap. Keep in mind
that pods only read env vars when they start. Editing a ConfigMap does **not**
restart the pods that use it, so the change won't show up until they are recreated.
(Look into Kustomize `configMapGenerator` or a checksum annotation when you get there.)

**Exposing it:** only the gateway needs to be reachable from outside. For a local
cluster, `kubectl port-forward svc/gateway 8080:80` is enough; later you can try an
Ingress or NodePort.

### Getting the images into the cluster

Argo CD pulls **manifests from Git**. It does not build or push images; the
kubelet on each node still pulls images from a registry. Two options:

**Local kind cluster (no registry needed):**

```bash
kind create cluster --name ridehail
for s in gateway rider-service driver-service trip-service pricing-service; do
  kind load docker-image ridehail/$s:1.0.0 --name ridehail
done
```

Then set `imagePullPolicy: IfNotPresent` in your Deployments. Otherwise Kubernetes
tries to pull `ridehail/...` from Docker Hub, which fails with `ErrImagePull`.

**A real registry (e.g. GHCR or ECR):** tag and push, then reference the full name:

```bash
docker tag ridehail/gateway:1.0.0 ghcr.io/<your-user>/ridehail-gateway:1.0.0
docker push ghcr.io/<your-user>/ridehail-gateway:1.0.0
```

A private registry also needs an `imagePullSecret` in the namespace.

### Argo CD reminders

- Argo CD reads manifests from a Git repo, so push this repo (plus your manifests) to
  GitHub first. A public repo avoids setting up repo credentials in Argo CD.
- Mind your context: `kubectl config current-context` should point at the practice
  cluster before you install anything.

## Things worth trying once it's deployed

These exercise the Argo CD concepts you're relearning:

1. **Git change → rollout:** change `SURGE_MULTIPLIER` in Git; fares in the UI change after sync.
2. **Image bump:** edit a service (e.g. the fare formula), build `1.1.0`, load or push it,
   bump the tag in Git, and watch the version change in the UI.
3. **Drift and self-heal:** `kubectl scale deploy/gateway --replicas=0` or `kubectl delete svc pricing-service`
   and watch Argo CD mark the app OutOfSync and, with self-heal on, repair it.
4. **Rollback:** roll back to a previous sync in Argo CD and compare it with `git revert`.
   Which one sticks when auto-sync is enabled?
5. **Break a dependency:** scale `pricing-service` to 0. `/api/status` shows it DOWN
   and booking fails with a 502. How does Argo CD's health status compare?
6. **Ordering:** use sync waves so the backends come up before the gateway, and add a
   PostSync hook Job that curls `http://gateway/api/status` as a smoke test.
7. **Multiple environments:** deploy dev and prod from the same repo (different namespaces,
   replicas, surge pricing), then try generating them with an ApplicationSet.
