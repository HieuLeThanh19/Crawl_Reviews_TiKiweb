"""
etl/celery_worker.py
Celery worker cho ETL bất đồng bộ.
Sử dụng Redis/RabbitMQ làm broker.

Khởi động:
    celery -A etl.celery_worker worker --loglevel=info --concurrency=4

Gửi task:
    from etl.celery_worker import process_review_task
    process_review_task.delay(review_dict)
"""
import logging

from celery import Celery

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import RABBITMQ_HOST, RABBITMQ_PORT, RABBITMQ_USER, RABBITMQ_PASS, REDIS_URL

logger = logging.getLogger(__name__)

# Cấu hình broker (RabbitMQ) và result backend (Redis)
BROKER_URL = f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASS}@{RABBITMQ_HOST}:{RABBITMQ_PORT}//"

app = Celery(
    "tiki_review_etl",
    broker=BROKER_URL,
    backend=REDIS_URL,
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Ho_Chi_Minh",
    enable_utc=True,
    task_acks_late=True,           # Chỉ ack sau khi xử lý xong
    worker_prefetch_multiplier=4,
    task_routes={
        "etl.celery_worker.process_review_task": {"queue": "etl_queue"},
        "etl.celery_worker.batch_process_task":  {"queue": "etl_queue"},
    },
)


@app.task(
    name="etl.celery_worker.process_review_task",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def process_review_task(self, review: dict) -> dict:
    """
    Task xử lý ETL cho một đánh giá.
    Retry tối đa 3 lần nếu gặp lỗi.
    """
    from etl.pipeline import ETLPipeline

    try:
        pipeline = ETLPipeline()
        success = pipeline.process_and_save(review)
        return {"success": success, "review_id": review.get("review_id")}
    except Exception as exc:
        logger.error(f"Task lỗi review {review.get('review_id')}: {exc}")
        raise self.retry(exc=exc)


@app.task(
    name="etl.celery_worker.batch_process_task",
    bind=True,
)
def batch_process_task(self, reviews: list) -> dict:
    """
    Task xử lý batch nhiều đánh giá cùng lúc.
    Hiệu quả hơn khi xử lý lô lớn.
    """
    from etl.pipeline import ETLPipeline

    pipeline = ETLPipeline()
    results = {"success": 0, "failed": 0}

    for review in reviews:
        try:
            ok = pipeline.process_and_save(review)
            if ok:
                results["success"] += 1
            else:
                results["failed"] += 1
        except Exception as e:
            logger.error(f"Batch lỗi: {e}")
            results["failed"] += 1

    return results


@app.task(name="etl.celery_worker.health_check_task")
def health_check_task() -> str:
    """Task kiểm tra worker còn sống."""
    import datetime
    return f"Worker OK @ {datetime.datetime.now().isoformat()}"
