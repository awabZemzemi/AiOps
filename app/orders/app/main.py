import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request

from .dependency import call_dependency
from .logging_setup import configure_logging
from .models import OrderConfirmation, OrderRequest


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(
        base_url=os.getenv("INVENTORY_URL", "http://localhost:8002"),
        timeout=2, trust_env=False,
    ) as client:
        app.state.inventory = client
        yield


app = FastAPI(title="SentinelShop Orders", lifespan=lifespan)
logger = configure_logging(app, "orders")


@app.post("/orders", response_model=OrderConfirmation, status_code=201)
async def orders(order: OrderRequest, request: Request):
    result = await call_dependency(
        request.app.state.inventory, logger, "Inventory", "POST",
        "/stock/decrement", order.model_dump(),
    )
    try:
        confirmation = OrderConfirmation(status="confirmed", **result)
    except (TypeError, ValueError) as exc:
        logger.error("Invalid inventory response")
        raise HTTPException(status_code=503, detail="Invalid inventory response") from exc
    logger.info("Order confirmed", extra={"fields": confirmation.model_dump()})
    return confirmation
