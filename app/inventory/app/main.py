from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from .logging_setup import configure_logging
from .models import OrderRequest
from .redis_client import (
    DECREMENT_STOCK, INITIAL_STOCK, create_redis_client,
    initialize_stock, stock_key,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = create_redis_client()
    app.state.redis = client
    try:
        try:
            await initialize_stock(client)
            logger.info("Stock initialized")
        except RedisError:
            logger.warning("Redis unavailable at startup", exc_info=True)
        yield
    finally:
        await client.aclose()


app = FastAPI(title="SentinelShop Inventory", lifespan=lifespan)
logger = configure_logging(app, "inventory")


@app.exception_handler(RedisError)
async def redis_error_handler(request: Request, exc: RedisError):
    logger.error("Redis request failed", extra={"fields": {
        "path": request.url.path, "error_type": type(exc).__name__,
    }})
    return JSONResponse(status_code=503, content={"detail": "Redis unavailable"})


@app.get("/redis-health")
async def redis_health(request: Request):
    await request.app.state.redis.ping()
    return {"status": "healthy"}


@app.get("/stock", response_model=dict[int, int])
async def stock(request: Request):
    client = request.app.state.redis
    await initialize_stock(client)
    values = await client.mget([stock_key(product_id) for product_id in INITIAL_STOCK])
    return {product_id: int(value) for product_id, value in zip(INITIAL_STOCK, values)}


@app.post("/stock/decrement", response_model=dict[str, int])
async def decrement(order: OrderRequest, request: Request):
    if order.product_id not in INITIAL_STOCK:
        raise HTTPException(status_code=404, detail="Product not found")
    client = request.app.state.redis
    await initialize_stock(client)
    remaining = await client.eval(
        DECREMENT_STOCK, 1, stock_key(order.product_id), order.quantity
    )
    if remaining < 0:
        raise HTTPException(status_code=409, detail="Insufficient stock")
    result = {
        "product_id": order.product_id,
        "quantity": order.quantity,
        "remaining_stock": remaining,
    }
    logger.info("Stock decremented", extra={"fields": result})
    return result
