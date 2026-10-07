import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request

from .dependency import call_dependency
from .logging_setup import configure_logging
from .models import Product


PRODUCTS = (
    {"id": 1, "name": "Laptop", "price": 999.00},
    {"id": 2, "name": "Keyboard", "price": 49.00},
    {"id": 3, "name": "Headphones", "price": 79.00},
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(
        base_url=os.getenv("INVENTORY_URL", "http://localhost:8002"),
        timeout=2, trust_env=False,
    ) as client:
        app.state.inventory = client
        yield


app = FastAPI(title="SentinelShop Catalog", lifespan=lifespan)
logger = configure_logging(app, "catalog")


@app.get("/products", response_model=list[Product])
async def products(request: Request):
    stocks = await call_dependency(
        request.app.state.inventory, logger, "Inventory", "GET", "/stock"
    )
    try:
        return [Product(**product, stock=stocks[str(product["id"])]) for product in PRODUCTS]
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("Invalid inventory response")
        raise HTTPException(status_code=503, detail="Invalid inventory response") from exc
