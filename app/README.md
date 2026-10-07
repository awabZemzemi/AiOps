# SentinelShop

A small Kubernetes/SRE lab with four independent Python/FastAPI microservices, a plain HTML/CSS/JavaScript frontend, and Redis: six containers total. Checkout is a thin forwarding service for another independently failing network hop. No payments, carts, authentication, queues, or Kubernetes manifests are included.

## Run locally

Install Docker with the Compose plugin, then run from this directory:

```bash
docker compose up --build
```

Open **http://localhost:8080**. Click **Order** to purchase one item or **Refresh stock** to reload stock. Serve the page through Compose rather than opening `index.html` directly.

When upgrading from the single-backend demo, use `docker compose up --build --remove-orphans` to remove its retired backend container. Port 8000 is no longer exposed. The existing `sentinelshop_redis-data` volume and stock keys remain compatible.

## Services and request flows

```text
Product list: Browser → Frontend/Nginx → Catalog → Inventory → Redis
Purchase:     Browser → Frontend/Nginx → Checkout → Orders → Inventory → Redis
```

| Service | Responsibility | Local address | Endpoints |
| --- | --- | --- | --- |
| frontend | Serve page and proxy browser API calls | http://localhost:8080 | `/api/products`, `/api/orders` |
| catalog | Own names/prices; fetch stock | http://localhost:8001 | `GET /products`, `GET /health` |
| inventory | Own Redis access and atomic decrement | http://localhost:8002 | `GET /stock`, `POST /stock/decrement`, `GET /redis-health`, `GET /health` |
| orders | Request stock decrement; confirm purchase | http://localhost:8003 | `POST /orders`, `GET /health` |
| checkout | Forward purchase to orders | http://localhost:8004 | `POST /orders`, `GET /health` |
| redis | Persist stock in an append-only Docker volume | Internal only | Redis protocol, port 6379 |

Every API listens on port 8000 **inside its container** and has interactive docs at its local `/docs` URL. Each service directory is an independent Docker build context. Only inventory installs the Redis client and receives Redis configuration.

Nginx routes `/api/products` to catalog and `/api/orders` to checkout, stripping `/api`. The browser API and UI are unchanged and use the same origin, so no CORS setup is needed. Nginx refreshes addresses through Docker DNS after container restarts/recreation; an unavailable proxied API returns a JSON 503 error.

| ID | Product | Price (USD) | Initial stock |
| --- | --- | --- | --- |
| 1 | Laptop | 999.00 | 10 |
| 2 | Keyboard | 49.00 | 20 |
| 3 | Headphones | 79.00 | 15 |

Inventory initializes missing `product:<id>:stock` keys using `SETNX`. Existing stock, including zero, survives API and Redis restarts. Inventory can start while Redis is unavailable and seed missing keys on later stock/decrement requests after recovery.

## Test APIs

```bash
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
curl http://localhost:8004/health
# Each returns {"status":"healthy"}, independently of dependencies

curl http://localhost:8002/redis-health
curl http://localhost:8002/stock
# Fresh stock: {"1":10,"2":20,"3":15}

curl http://localhost:8001/products
curl http://localhost:8080/api/products

curl -i http://localhost:8080/api/orders \
  -H 'Content-Type: application/json' \
  -d '{"product_id":1,"quantity":1}'
# HTTP 201; on fresh stock:
# {"status":"confirmed","product_id":1,"quantity":1,"remaining_stock":9}

# Either purchase API accepts the same payload directly:
curl -i http://localhost:8004/orders \
  -H 'Content-Type: application/json' \
  -d '{"product_id":2,"quantity":1}'
curl -i http://localhost:8003/orders \
  -H 'Content-Type: application/json' \
  -d '{"product_id":2,"quantity":1}'

# Inventory's internal write API also changes stock:
curl -i http://localhost:8002/stock/decrement \
  -H 'Content-Type: application/json' \
  -d '{"product_id":3,"quantity":1}'
# HTTP 200: {"product_id":3,"quantity":1,"remaining_stock":14}

curl -i http://localhost:8080/api/orders \
  -H 'Content-Type: application/json' \
  -d '{"product_id":1,"quantity":1000}'
# HTTP 409: Insufficient stock
```

IDs and quantities must be positive integers. Invalid input returns **422**, unknown products **404**, insufficient stock **409**, and unavailable dependencies **503**. Errors pass back through the service chain. Stock checks and decrements use a single atomic Redis script to prevent overselling.

HTTP dependencies have a two-second request deadline and two-second connect/read timeouts; Redis has two-second connect/read timeouts. No purchase request is automatically retried. **A timed-out purchase may already have reduced stock.** Idempotency, order storage, and retry recovery are outside this demo.

## Failure exercises

Run one exercise at a time and restore each service afterward. All running APIs' `/health` endpoints remain healthy when a dependency fails.

| Stop command | Expected behavior | Restore command |
| --- | --- | --- |
| `docker compose stop redis` | Inventory `/redis-health`, listing, and purchases return 503 | `docker compose start redis` |
| `docker compose stop inventory` | Listing and purchases return 503 | `docker compose start inventory` |
| `docker compose stop orders` | Purchases return 503; listing works | `docker compose start orders` |
| `docker compose stop checkout` | Browser purchases return JSON 503; direct orders and listing work | `docker compose start checkout` |
| `docker compose stop catalog` | Browser listing returns JSON 503; purchases work | `docker compose start catalog` |

```bash
docker compose stop redis
curl -i http://localhost:8002/health        # 200
curl -i http://localhost:8002/redis-health  # 503
curl -i http://localhost:8080/api/products  # 503
docker compose restart inventory          # Starts even while Redis is stopped
docker compose start redis
curl http://localhost:8080/api/products    # Recovers after Redis starts
```

Services receive `INVENTORY_URL` (catalog/orders) or `ORDERS_URL` (checkout). Only inventory receives `REDIS_HOST` and `REDIS_PORT`. Compose supports host-side overrides for isolated failures:

| Compose override | Container setting | Default |
| --- | --- | --- |
| `CATALOG_INVENTORY_URL` | Catalog `INVENTORY_URL` | `http://inventory:8000` |
| `ORDERS_INVENTORY_URL` | Orders `INVENTORY_URL` | `http://inventory:8000` |
| `CHECKOUT_ORDERS_URL` | Checkout `ORDERS_URL` | `http://orders:8000` |
| `REDIS_HOST` | Inventory `REDIS_HOST` | `redis` |
| `REDIS_PORT` | Inventory `REDIS_PORT` | `6379` |

```bash
# Wrong downstream port: checkout is healthy but purchases fail with 503.
CHECKOUT_ORDERS_URL=http://orders:9999 docker compose up -d --no-deps --force-recreate checkout
curl -i http://localhost:8004/health
curl -i http://localhost:8004/orders \
  -H 'Content-Type: application/json' \
  -d '{"product_id":1,"quantity":1}'

# Restore defaults; ensure the override isn't exported in your shell/.env.
docker compose up -d --no-deps --force-recreate checkout
```

Likewise, override `CATALOG_INVENTORY_URL` or `ORDERS_INVENTORY_URL` with a bad hostname/port and recreate that service. Set `REDIS_HOST=missing` to make inventory's Redis requests fail, or `REDIS_PORT=invalid` to make inventory fail startup. Restore by recreating the service with normal settings. Malformed HTTP base URLs may also prevent API startup.

## Automated lab verification and logs

With the demo running and no other users placing orders:

```bash
python3 tests/test_lab.py
```

The test uses only Python's standard library and Docker Compose. It checks API contracts, proxy routing, concurrent stock depletion, outages, misconfiguration, and restart recovery. It temporarily changes stock and stops/recreates containers, then restores stock and normal settings. A Docker-accessible shell is required. Use only against this local demo.

Application logs are JSON lines with service, timestamp, level, method, path, status, and duration; purchase logs include product, quantity, and remaining stock. Uvicorn also emits its usual logs.

```bash
docker compose logs -f catalog inventory orders checkout
```

## Stop or reset

```bash
docker compose down
```

To discard stock and restore initial quantities on next startup:

```bash
docker compose down -v
docker compose up --build
```
