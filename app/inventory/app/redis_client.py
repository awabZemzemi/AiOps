import os

from redis.asyncio import Redis


INITIAL_STOCK = {1: 10, 2: 20, 3: 15}

# Check and decrement in one operation, including under concurrent requests.
DECREMENT_STOCK = """
local stock = tonumber(redis.call('GET', KEYS[1]))
local quantity = tonumber(ARGV[1])
if not stock or stock < quantity then
    return -1
end
return redis.call('DECRBY', KEYS[1], quantity)
"""


def create_redis_client() -> Redis:
    return Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def stock_key(product_id: int) -> str:
    return f"product:{product_id}:stock"


async def initialize_stock(client: Redis) -> None:
    # SETNX preserves existing stock, including zero, across backend restarts.
    async with client.pipeline(transaction=False) as pipeline:
        for product_id, stock in INITIAL_STOCK.items():
            pipeline.setnx(stock_key(product_id), stock)
        await pipeline.execute()
