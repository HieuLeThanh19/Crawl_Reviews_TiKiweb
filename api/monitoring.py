"""
api/monitoring.py
Prometheus metrics và health monitoring cho hệ thống.

Tích hợp vào FastAPI:
    from api.monitoring import setup_metrics
    setup_metrics(app)

Metrics exposed tại: GET /metrics
"""
import time
import logging
from functools import wraps

logger = logging.getLogger(__name__)

# Lazy import prometheus_client
try:
    from prometheus_client import (
        Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
    )
    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False
    logger.warning("prometheus_client chưa cài. Chạy: pip install prometheus-client")


# ── Định nghĩa metrics ────────────────────────────────────────────────────────
if _HAS_PROMETHEUS:
    # Số request API
    REQUEST_COUNT = Counter(
        "tiki_api_requests_total",
        "Tổng số HTTP request đến API",
        ["method", "endpoint", "status_code"],
    )
    # Độ trễ response
    REQUEST_LATENCY = Histogram(
        "tiki_api_request_duration_seconds",
        "Thời gian xử lý HTTP request (giây)",
        ["endpoint"],
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
    )
    # Số đánh giá đã crawl
    REVIEWS_CRAWLED = Counter(
        "tiki_reviews_crawled_total",
        "Tổng số đánh giá đã thu thập",
        ["product_id", "status"],
    )
    # Số đánh giá trong queue
    QUEUE_SIZE = Gauge(
        "tiki_queue_size",
        "Số message đang chờ trong RabbitMQ queue",
    )
    # Số đánh giá đã xử lý ETL
    ETL_PROCESSED = Counter(
        "tiki_etl_processed_total",
        "Số đánh giá đã qua pipeline ETL",
        ["status"],  # success | failed | skipped
    )
    # Thời gian xử lý ETL
    ETL_LATENCY = Histogram(
        "tiki_etl_duration_seconds",
        "Thời gian xử lý ETL mỗi đánh giá",
        buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
    )


# ── Decorators ────────────────────────────────────────────────────────────────
def track_etl_metrics(func):
    """Decorator tự động đo thời gian và kết quả ETL."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _HAS_PROMETHEUS:
            return func(*args, **kwargs)

        start = time.time()
        try:
            result = func(*args, **kwargs)
            duration = time.time() - start
            ETL_LATENCY.observe(duration)
            if result:
                ETL_PROCESSED.labels(status="success").inc()
            else:
                ETL_PROCESSED.labels(status="skipped").inc()
            return result
        except Exception as e:
            ETL_PROCESSED.labels(status="failed").inc()
            raise
    return wrapper


# ── FastAPI integration ───────────────────────────────────────────────────────
def setup_metrics(app):
    """
    Gắn Prometheus metrics middleware và endpoint /metrics vào FastAPI app.

    Sử dụng:
        from fastapi import FastAPI
        from api.monitoring import setup_metrics

        app = FastAPI()
        setup_metrics(app)
    """
    if not _HAS_PROMETHEUS:
        logger.warning("Bỏ qua setup metrics — prometheus_client chưa cài.")
        return

    from fastapi import Request
    from fastapi.responses import Response
    from starlette.middleware.base import BaseHTTPMiddleware

    class MetricsMiddleware(BaseHTTPMiddleware):
        """Middleware tự động đo latency và đếm request."""
        async def dispatch(self, request: Request, call_next):
            start = time.time()
            response = await call_next(request)
            duration = time.time() - start

            endpoint = request.url.path
            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=endpoint,
                status_code=response.status_code,
            ).inc()
            REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)
            return response

    app.add_middleware(MetricsMiddleware)

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        """Expose Prometheus metrics."""
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    logger.info("Prometheus metrics đã được thiết lập tại /metrics")


# ── Helper functions ─────────────────────────────────────────────────────────
def record_crawl(product_id: str, count: int, status: str = "success"):
    """Ghi nhận số review đã crawl."""
    if _HAS_PROMETHEUS:
        REVIEWS_CRAWLED.labels(product_id=product_id, status=status).inc(count)


def update_queue_size(size: int):
    """Cập nhật số message trong queue."""
    if _HAS_PROMETHEUS:
        QUEUE_SIZE.set(size)
