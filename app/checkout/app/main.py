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
        base_url=os.getenv("ORDERS_URL", "http://localhost:8003"),
        timeout=2, trust_env=False,
    ) as client:
        app.state.orders = client
        yield


app = FastAPI(title="SentinelShop Checkout", lifespan=lifespan)
logger = configure_logging(app, "checkout")


@app.post("/orders", response_model=OrderConfirmation, status_code=201)
async def checkout(order: OrderRequest, request: Request):
    result = await call_dependency(
        request.app.state.orders, logger, "Orders", "POST", "/orders", order.model_dump()
    )
    try:
        confirmation = OrderConfirmation(**result)
    except (TypeError, ValueError) as exc:
        logger.error("Invalid orders response")
        raise HTTPException(status_code=503, detail="Invalid orders response") from exc
    logger.info("Checkout completed", extra={"fields": confirmation.model_dump()})
    return confirmation
