import asyncio

import httpx
from fastapi import HTTPException


async def call_dependency(client, logger, service, method, path, payload=None):
    """Make one HTTP request; never retry a potentially committed purchase."""
    try:
        async with asyncio.timeout(2):
            response = await client.request(method, path, json=payload)
        data = response.json()
    except (httpx.RequestError, TimeoutError, ValueError) as exc:
        logger.warning("Dependency unavailable", extra={"fields": {
            "dependency": service, "error_type": type(exc).__name__,
        }})
        raise HTTPException(status_code=503, detail=f"{service} unavailable") from exc

    if response.status_code in (404, 409, 422, 503) and isinstance(data, dict):
        raise HTTPException(
            status_code=response.status_code,
            detail=data.get("detail", f"{service} request failed"),
        )
    if not response.is_success:
        logger.error("Dependency request failed", extra={"fields": {
            "dependency": service, "status_code": response.status_code,
        }})
        raise HTTPException(status_code=503, detail=f"{service} unavailable")
    return data
