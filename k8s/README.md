# SentinelShop Kubernetes lab

For a configurable Helm deployment, use the [SentinelShop chart](../helm/sentinelshop/README.md).

Run commands from the repository root (`AiOps/`). The application code stays in `app/`; these manifests define its Kubernetes runtime. Start with one replica per component to make failures visible.

## What each file creates

| File | Resources and reason |
| --- | --- |
| `namespace.yaml` | A `aiops` Namespace to group the lab. |
| `app-configmap.yaml` | Downstream URLs and Redis configuration, injected selectively into APIs. |
| `frontend-configmap.yaml` | Nginx proxy template and startup script that read DNS from the Pod's `/etc/resolv.conf`. |
| `frontend.yaml` | Deployment serving the page and forwarding `/api/products` to catalog and `/api/orders` to checkout; internal Service on port 80. |
| `catalog.yaml` | Deployment combining product details with inventory stock; internal Service on port 8000. |
| `inventory.yaml` | Deployment owning all Redis access; internal Service on port 8000. |
| `orders.yaml` | Deployment requesting stock decrements and returning confirmations; internal Service on port 8000. |
| `checkout.yaml` | Deployment forwarding purchases to orders; internal Service on port 8000. |
| `redis.yaml` | Single-replica StatefulSet, client Service, headless identity Service, and a 1Gi persistent volume claim template. |

A Deployment replaces failed application Pods and manages updates. A Service selects Pods by label and gives callers a stable DNS name. Keep these workloads separate so each can be broken and recovered independently.

```text
Browser → frontend → catalog → inventory → Redis
Browser → frontend → checkout → orders → inventory → Redis
```

## Build images and make them available

```bash
for service in frontend catalog inventory orders checkout; do
  docker build -t "sentinelshop-$service:v1" "app/$service"
done
```

The manifests use these image names with `imagePullPolicy: IfNotPresent`. Docker images on your workstation must also be available to the Kubernetes nodes.

For the existing kind cluster named `sentinelops` (context `kind-sentinelops`):

```bash
kind load docker-image \
  sentinelshop-frontend:v1 sentinelshop-catalog:v1 \
  sentinelshop-inventory:v1 sentinelshop-orders:v1 \
  sentinelshop-checkout:v1 --name sentinelops
```

For another kind cluster, substitute its name. For minikube, use `minikube image load` for each image. For a remote cluster, push images to an accessible registry and replace the five Deployment image references with their full registry names. A private registry also requires appropriate image pull credentials.

When rebuilding, use a new tag, load/push it, and update the Deployment image; this avoids reusing a cached old image.

## Deploy

Check the current context and storage provisioner first:

```bash
kubectl config current-context
kubectl get storageclass
```

Redis's claim uses the default StorageClass. Without a default provisioner or a matching pre-created volume, the claim stays Pending. The existing kind cluster has a default `standard` StorageClass backed by local-path storage.

Apply the namespace first, then the resources:

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/
kubectl -n aiops rollout status statefulset/redis --timeout=120s
for service in frontend catalog inventory orders checkout; do
  kubectl -n aiops rollout status "deployment/$service" --timeout=120s
done
kubectl -n aiops get pods,services,pvc
```

Applications tolerate downstream outages; Kubernetes does not enforce Compose-style `depends_on`. Readiness controls whether a running Pod receives Service traffic.

All Services are internal. Open the frontend through a local tunnel:

```bash
kubectl -n aiops port-forward service/frontend 8080:80
```

Open **http://localhost:8080**. If Compose is still occupying port 8080, stop the Compose demo (`cd app && docker compose down`) or use `8081:80` for the tunnel. A port-forward can need restarting after the Pod it selected is replaced; that is separate from in-cluster Service routing.

The four APIs listen on port 8000 in their Pods; Redis listens on 6379. Compose's host ports 8001–8004 are not used here. For direct inventory access in another terminal:

```bash
kubectl -n aiops port-forward service/inventory 8002:8000
curl http://localhost:8002/health
curl http://localhost:8002/redis-health
curl http://localhost:8002/stock
```

## Verify behavior

With the frontend tunnel running:

```bash
curl http://localhost:8080/api/products
curl -i http://localhost:8080/api/orders \
  -H 'Content-Type: application/json' \
  -d '{"product_id":1,"quantity":1}'
# 201; on fresh stock, remaining_stock is 9

curl -i http://localhost:8080/api/orders \
  -H 'Content-Type: application/json' \
  -d '{"product_id":1,"quantity":1000}'
# 409: Insufficient stock

kubectl -n aiops logs deployment/orders --tail=20
kubectl -n aiops get events --sort-by=.metadata.creationTimestamp
```

Unknown products return 404, invalid quantities return 422, and dependency failures return 503. The two-second HTTP deadlines and no-retry purchase behavior remain in the application. A timeout can occur after stock has already been decremented; idempotency is outside this demo.

The existing `app/tests/test_lab.py` exercises Compose and manages Compose containers; it is not a Kubernetes test runner.

## Probes, resources, and storage

| Component | Startup and liveness | Readiness |
| --- | --- | --- |
| frontend | HTTP `/` on 80 | HTTP `/` on 80 |
| catalog, orders, checkout | HTTP `/health` on 8000 | HTTP `/health` on 8000 |
| inventory | HTTP `/health` on 8000 | HTTP `/redis-health` on 8000; timeout 3 seconds |
| Redis | Execute `redis-cli ping` | Execute `redis-cli ping` |

Startup probes allow roughly 60 seconds before liveness/readiness checks begin. Failed liveness checks restart the container; failed readiness checks remove it from Service traffic. API liveness checks deliberately do not depend on Redis. Catalog/orders/checkout readiness currently checks only the local process; those Pods can remain Ready while requests return 503 from downstream failures.

API requests are 100m CPU and 128Mi memory, with limits of 500m CPU and 256Mi memory. Frontend and Redis use smaller requests. These are initial lab values to measure and adjust. Memory limits support OOMKilled exercises; CPU limits can cause throttling.

Redis runs with append-only persistence and mounts its PVC at `/data`. Replacing its Pod preserves stock, including zero. The Docker Compose volume is separate and is not imported into Kubernetes. Increasing Redis replicas alone creates independent instances, not replication; leave it at one.

Local-path storage is suitable for this local lab. It does not provide storage failover or preserve data if the kind cluster itself is deleted.

## Failure exercises and recovery

Introduce one fault at a time, inspect it, then restore it using the command shown. Use `kubectl apply -f k8s/` to restore manifest edits, and restart affected workloads when configuration files/environment need reloading.

### Redis unavailable

```bash
kubectl -n aiops scale statefulset/redis --replicas=0
kubectl -n aiops get pods
curl -i http://localhost:8080/api/products
# 503 once Redis/inventory become unavailable
kubectl -n aiops scale statefulset/redis --replicas=1
kubectl -n aiops rollout status statefulset/redis --timeout=120s
```

Inventory stays alive but becomes unready. Catalog and checkout still run; requests requiring inventory fail. Existing Redis stock returns when Redis starts.

### Incorrect environment variable

```bash
kubectl -n aiops set env deployment/checkout ORDERS_URL=http://orders:9999
kubectl -n aiops rollout status deployment/checkout --timeout=120s
curl -i http://localhost:8080/api/orders \
  -H 'Content-Type: application/json' \
  -d '{"product_id":1,"quantity":1}'
# 503; product listing remains available
kubectl -n aiops rollout undo deployment/checkout
kubectl -n aiops rollout status deployment/checkout --timeout=120s
```

The undo command restores the previous Deployment revision, including the ConfigMap reference replaced by `set env`.

### CrashLoopBackOff

```bash
kubectl -n aiops set env deployment/inventory REDIS_PORT=invalid
kubectl -n aiops get pods
# Inspect the newly failing inventory Pod:
kubectl -n aiops logs <inventory-pod-name> --previous
kubectl -n aiops describe pod <inventory-pod-name>
kubectl -n aiops rollout undo deployment/inventory
kubectl -n aiops rollout status deployment/inventory --timeout=120s
```

A rolling Deployment can retain its old healthy Pod while the new Pod crashes; inspect both. CrashLoopBackOff appears after repeated restarts, not necessarily immediately.

### Incorrect Service port

```bash
kubectl -n aiops patch service/orders --type=merge \
  -p '{"spec":{"ports":[{"name":"http","port":8000,"targetPort":9999}]}}'
# Orders Pod health can remain good while checkout cannot reach it.
kubectl apply -f k8s/orders.yaml
```

### Readiness and liveness failures

```bash
kubectl -n aiops patch deployment/catalog --type=strategic \
  -p '{"spec":{"template":{"spec":{"containers":[{"name":"catalog","readinessProbe":{"httpGet":{"path":"/wrong-path"}}}]}}}}'
# New Pod stays Running but unready. The old ready Pod may still serve requests.
kubectl apply -f k8s/catalog.yaml

kubectl -n aiops patch deployment/orders --type=strategic \
  -p '{"spec":{"template":{"spec":{"containers":[{"name":"orders","livenessProbe":{"httpGet":{"path":"/wrong-path"}}}]}}}}'
# New Pod restarts after repeated liveness failures.
kubectl apply -f k8s/orders.yaml
```

### OOMKilled

```bash
kubectl -n aiops set resources deployment/orders \
  --requests=cpu=100m,memory=16Mi --limits=cpu=500m,memory=16Mi
# Inspect the new Pod's termination reason; exact memory usage varies.
kubectl -n aiops describe pod <orders-pod-name>
kubectl apply -f k8s/orders.yaml
```

If the workload fits in 16Mi on your system, adjust the exercise limit. Low-memory failures can also appear during container startup; verify the actual termination reason instead of assuming every crash is OOMKilled.

ConfigMap-backed environment variables are read when containers start. After editing `app-configmap.yaml`, apply it and restart the affected API Deployment. The frontend template is rendered once at startup; after editing its ConfigMap, run `kubectl -n aiops rollout restart deployment/frontend`.

## Remove the lab

```bash
kubectl delete namespace aiops
```

This removes the namespace and its PVC. With the kind cluster's `Delete` reclaim policy, the backing Redis storage is also deleted. Use workload scaling rather than namespace deletion when you want to preserve stock.
