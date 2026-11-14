"""Diagnostic routes included in the unified backend application."""
import logging
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query

from diagnostics import DEFAULT_LOG_LINES, MAX_LOG_LINES, PodEvidenceError, collect_pod_evidence

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


@router.get("/api/v1/pods/{namespace}/{pod_name}/evidence", responses={
    403: {"description": "Kubernetes access to the target is forbidden"},
    404: {"description": "Target Pod does not exist"},
    429: {"description": "Kubernetes API rate limit exceeded"},
    502: {"description": "Kubernetes rejected the request"},
    503: {"description": "Kubeconfig, authentication, or Kubernetes API unavailable"},
    500: {"description": "Unexpected collection failure"},
})
def pod_evidence(
    namespace: Namespace,
    pod_name: PodName,
    tail_lines: Annotated[int, Query(
        ge=1, le=MAX_LOG_LINES, description="Maximum lines per container log stream",
    )] = DEFAULT_LOG_LINES,
) -> dict:
    """Return the collector report unchanged, including any partial collection errors."""
    started = perf_counter()
    logger.info("evidence_started namespace=%s pod=%s tail_lines=%s", namespace, pod_name, tail_lines)
    try:
        report = collect_pod_evidence(namespace, pod_name, tail_lines=tail_lines)
    except PodEvidenceError as error:
        # Never return str(error), its cause, or Kubernetes API response bodies.
        status_code, detail = {
            400: (502, "Kubernetes rejected the evidence request."),
            403: (403, "Access to the target Pod is forbidden."),
            404: (404, "Pod not found."),
            429: (429, "Kubernetes API rate limit exceeded."),
        }.get(error.status, (503, "Kubernetes evidence collection is unavailable."))
        logger.warning("evidence_failed namespace=%s pod=%s http_status=%s", namespace, pod_name, status_code)
        raise HTTPException(status_code=status_code, detail=detail) from None
    except Exception:
        # Handle here: a global Exception handler can still be re-raised to Uvicorn,
        # which would log the original traceback and potentially sensitive messages.
        logger.error("evidence_unexpected_error namespace=%s pod=%s", namespace, pod_name)
        raise HTTPException(status_code=500, detail="Evidence collection failed.") from None
    error_count = len(report["collection_errors"])
    if error_count:
        logger.warning("evidence_partial namespace=%s pod=%s error_count=%s", namespace, pod_name, error_count)
    logger.info("evidence_completed namespace=%s pod=%s error_count=%s duration_ms=%.1f",
                namespace, pod_name, error_count, (perf_counter() - started) * 1000)
    return report
