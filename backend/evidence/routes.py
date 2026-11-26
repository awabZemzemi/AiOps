"""Diagnostic routes included in the unified backend application."""
import logging
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from backend.auth.dependencies import namespace_access

from diagnostic_service.diagnostics import DEFAULT_LOG_LINES, MAX_LOG_LINES, PodEvidenceError, collect_pod_evidence
from diagnostic_service.security import sanitize_evidence
from diagnostic_service.security.schemas import PodReport

router = APIRouter()
# Reuse Uvicorn's configured handler/level without changing global logging configuration.
logger = logging.getLogger("uvicorn.error.aiops")

# Kubernetes namespace names are DNS labels; Pod names may contain dot-separated labels.
Namespace = Annotated[str, Path(
    min_length=1, max_length=63, pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
    description="Kubernetes namespace, e.g. aiops",
)]
PodName = Annotated[str, Path(
    min_length=1, max_length=253,
    pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$",
    description="Exact Pod name, not a Deployment or Service name",
)]


@router.get("/health")
def health() -> dict:
    """Process health only; does not contact Kubernetes."""
    return {"status": "ok"}


@router.get("/api/v1/pods/{namespace}/{pod_name}/evidence",
            response_model=PodReport, dependencies=[Depends(namespace_access)], responses={
    403: {"description": "Kubernetes access to the target is forbidden"},
    404: {"description": "Target Pod does not exist"},
    429: {"description": "Kubernetes API rate limit exceeded"},
    502: {"description": "Kubernetes rejected the request"},
    503: {"description": "Evidence collection or safe processing unavailable"},
    500: {"description": "Unexpected collection failure"},
})
def pod_evidence(
    namespace: Namespace,
    pod_name: PodName,
    tail_lines: Annotated[int, Query(
        ge=1, le=MAX_LOG_LINES, description="Maximum lines per container log stream",
    )] = DEFAULT_LOG_LINES,
) -> JSONResponse:
    """Return a sanitized report, including any partial collection errors."""
    started = perf_counter()
    logger.info("evidence_started tail_lines=%s", tail_lines)
    try:
        report = collect_pod_evidence(namespace, pod_name, tail_lines=tail_lines)
        safe = sanitize_evidence("pod_report", report, destination="api",
                                 target={"namespace": namespace, "pod_name": pod_name})
        if safe is None:
            logger.warning("evidence_withheld")
            return JSONResponse(status_code=503, content={"detail": "Evidence cannot be safely returned."})
        response = JSONResponse(content=safe)
    except PodEvidenceError as error:
        # Never return str(error), its cause, or Kubernetes API response bodies.
        status_code, detail = {
            400: (502, "Kubernetes rejected the evidence request."),
            403: (403, "Access to the target Pod is forbidden."),
            404: (404, "Pod not found."),
            429: (429, "Kubernetes API rate limit exceeded."),
        }.get(error.status if type(error.status) is int else None,
              (503, "Kubernetes evidence collection is unavailable."))
        logger.warning("evidence_failed http_status=%s", status_code)
        raise HTTPException(status_code=status_code, detail=detail) from None
    except Exception:
        # Handle here: a global Exception handler can still be re-raised to Uvicorn,
        # which would log the original traceback and potentially sensitive messages.
        logger.error("evidence_unexpected_error")
        raise HTTPException(status_code=500, detail="Evidence collection failed.") from None
    error_count = len(safe["collection_errors"])
    if error_count:
        logger.warning("evidence_partial error_count=%s", error_count)
    logger.info("evidence_completed error_count=%s duration_ms=%.1f", error_count, (perf_counter() - started) * 1000)
    return response
