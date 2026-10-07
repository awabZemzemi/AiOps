"""Opt-in integration test: changes stock and restarts local demo containers."""

import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


ROOT = Path(__file__).resolve().parents[1]
PORTS = {"catalog": 8001, "inventory": 8002, "orders": 8003, "checkout": 8004}
OVERRIDES = {
    "CATALOG_INVENTORY_URL", "ORDERS_INVENTORY_URL", "CHECKOUT_ORDERS_URL",
    "REDIS_HOST", "REDIS_PORT",
}


def compose(*args, overrides=None):
    env = os.environ.copy()
    for key in OVERRIDES:
        env.pop(key, None)
    env.update(overrides or {})
    return subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, env=env,
        check=True, capture_output=True, text=True,
    ).stdout


def request(port, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://localhost:{port}{path}", data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read().decode()
        return response.status, json.loads(body) if body.startswith(("{", "[")) else body


def expect(port, path, status, payload=None):
    actual, body = request(port, path, payload)
    assert actual == status, (port, path, actual, body)
    return body


def wait_ready(port, path="/health", status=200, payload=None):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            return expect(port, path, status, payload)
        except (OSError, AssertionError):
            time.sleep(0.5)
    raise AssertionError(f"{port}{path} did not recover")


def healthy_except(stopped=None):
    for service, port in PORTS.items():
        if service != stopped:
            assert expect(port, "/health", 200) == {"status": "healthy"}


def set_stock(stocks):
    args = ["exec", "-T", "redis", "redis-cli", "MSET"]
    for product_id, count in stocks.items():
        args.extend((f"product:{product_id}:stock", str(count)))
    assert compose(*args).strip() == "OK"


def recreate(service, overrides=None):
    compose("up", "-d", "--no-deps", "--force-recreate", service, overrides=overrides)
    wait_ready(PORTS[service])


def verify_request_deadline():
    # Run inside the API image to exercise its installed HTTP client and helper.
    script = '''
import asyncio
import logging
import time
import httpx
from fastapi import HTTPException
from app.dependency import call_dependency

async def verify():
    attempts = 0
    async def slow_purchase(request):
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(10)
        return httpx.Response(200, json={})
    async with httpx.AsyncClient(
        base_url="http://inventory", transport=httpx.MockTransport(slow_purchase)
    ) as client:
        started = time.monotonic()
        try:
            await call_dependency(client, logging.getLogger("test"), "Inventory",
                                  "POST", "/stock/decrement", {"product_id": 1, "quantity": 1})
        except HTTPException as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError("Slow purchase did not time out")
        assert 1.8 <= time.monotonic() - started < 3
        assert attempts == 1, "Purchase was retried"
asyncio.run(verify())
'''
    compose("exec", "-T", "orders", "python", "-c", script)
    print("PASS: two-second dependency deadline and no purchase retries", flush=True)


def verify():
    for port in PORTS.values():
        wait_ready(port)
    original_stock = wait_ready(8002, "/stock")
    order = {"product_id": 1, "quantity": 1}
    try:
        verify_request_deadline()
        set_stock({1: 10, 2: 20, 3: 15})
        healthy_except()
        assert expect(8002, "/redis-health", 200) == {"status": "healthy"}
        products = expect(8001, "/products", 200)
        assert [(p["name"], p["price"], p["stock"]) for p in products] == [
            ("Laptop", 999, 10), ("Keyboard", 49, 20), ("Headphones", 79, 15)
        ]
        assert expect(8080, "/api/products?test=proxy", 200) == products
        assert "SentinelShop" in expect(8080, "/", 200)
        expect(8080, "/app.js", 200)
        expect(8080, "/styles.css", 200)
        assert expect(8080, "/api/orders", 201, order) == {
            "status": "confirmed", "product_id": 1, "quantity": 1, "remaining_stock": 9,
        }
        expect(8003, "/orders", 201, order)
        expect(8004, "/orders", 201, order)
        expect(8002, "/stock/decrement", 200, order)
        assert expect(8002, "/stock", 200)["1"] == 6
        for port, path in [(8080, "/api/orders"), (8004, "/orders"),
                           (8003, "/orders"), (8002, "/stock/decrement")]:
            expect(port, path, 404, {"product_id": 99, "quantity": 1})
            expect(port, path, 409, {"product_id": 1, "quantity": 1000})
            for invalid in (0, -1, 1.5, True, "1"):
                expect(port, path, 422, {"product_id": 1, "quantity": invalid})
                expect(port, path, 422, {"product_id": invalid, "quantity": 1})
            expect(port, path, 422, {})
        with ThreadPoolExecutor(max_workers=15) as pool:
            responses = list(pool.map(
                lambda _: request(8080, "/api/orders", {"product_id": 3, "quantity": 1}),
                range(30),
            ))
        assert sum(status == 201 for status, _ in responses) == 15, responses
        assert sum(status == 409 for status, _ in responses) == 15, responses
        assert expect(8002, "/stock", 200)["3"] == 0
        print("PASS: contracts, proxy, validation, and concurrent ordering", flush=True)

        depleted = expect(8002, "/stock", 200)
        compose("restart", "redis", "inventory", "orders", "checkout", "catalog")
        assert wait_ready(8002, "/stock") == depleted
        wait_ready(8080, "/api/products")
        wait_ready(8080, "/api/orders", 409, {"product_id": 3, "quantity": 1})
        print("PASS: restart persistence, zero stock, and proxy DNS recovery", flush=True)

        for service in ("redis", "inventory", "orders", "checkout", "catalog"):
            compose("stop", service)
            try:
                healthy_except(service)
                if service in ("redis", "inventory"):
                    expect(8080, "/api/products", 503)
                    expect(8080, "/api/orders", 503, order)
                elif service in ("orders", "checkout"):
                    expect(8080, "/api/products", 200)
                    assert isinstance(expect(8080, "/api/orders", 503, order)["detail"], str)
                    if service == "checkout":
                        expect(8003, "/orders", 201, {"product_id": 2, "quantity": 1})
                else:
                    expect(8080, "/api/products", 503)
                    expect(8080, "/api/orders", 201, {"product_id": 2, "quantity": 1})
                if service == "redis":
                    expect(8002, "/redis-health", 503)
                    compose("restart", "inventory")
                    wait_ready(8002)
                    expect(8002, "/stock", 503)
            finally:
                compose("start", service)
                wait_ready(8080, "/api/products")
                for port in PORTS.values():
                    wait_ready(port)
                wait_ready(8080, "/api/orders", 409, {"product_id": 3, "quantity": 1})
            print(f"PASS: {service} outage and recovery", flush=True)

        for service, variable, target, path in (
            ("catalog", "CATALOG_INVENTORY_URL", "http://missing:8000", "/products"),
            ("orders", "ORDERS_INVENTORY_URL", "http://inventory:9999", "/orders"),
            ("checkout", "CHECKOUT_ORDERS_URL", "http://orders:9999", "/orders"),
        ):
            try:
                recreate(service, {variable: target})
                expect(PORTS[service], path, 503, None if path == "/products" else order)
                healthy_except()
            finally:
                recreate(service)
            print(f"PASS: {service} downstream misconfiguration", flush=True)
        try:
            recreate("inventory", {"REDIS_HOST": "missing"})
            expect(8002, "/redis-health", 503)
            healthy_except()
        finally:
            recreate("inventory")
        try:
            compose("up", "-d", "--no-deps", "--force-recreate", "inventory",
                    overrides={"REDIS_PORT": "invalid"})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                containers = json.loads(compose("ps", "-a", "--format", "json", "inventory"))
                if isinstance(containers, dict):
                    containers = [containers]
                if containers and containers[0]["State"] == "exited":
                    assert containers[0]["ExitCode"] != 0
                    break
                time.sleep(0.5)
            else:
                raise AssertionError("Invalid Redis port did not fail startup")
        finally:
            recreate("inventory")
        print("PASS: Redis hostname error and invalid-port startup failure", flush=True)

        healthy_except()
        wait_ready(8080, "/api/products")
        wait_ready(8080, "/api/orders", 409, {"product_id": 3, "quantity": 1})
        for service in PORTS:
            logs = compose("logs", "--no-log-prefix", service)
            assert any(json.loads(line).get("service") == service
                       for line in logs.splitlines() if line.startswith("{"))
        print("PASS: structured service logs and final recovery", flush=True)
    finally:
        compose("start", "redis")
        wait_ready(8002, "/redis-health")
        set_stock(original_stock)
        assert wait_ready(8002, "/stock") == original_stock
        print("Original stock restored", flush=True)


if __name__ == "__main__":
    verify()
