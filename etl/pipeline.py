"""
etl/pipeline.py
Pipeline ETL tổng hợp: Extract → Transform → Load.

Luồng xử lý:
  1. Extract: Đọc đánh giá thô từ RabbitMQ hoặc JSON file
  2. Transform:
     a. Làm sạch văn bản (cleaner.py)
     b. Xử lý NLP (nlp_processor.py)
  3. Load: Lưu vào PostgreSQL

Có thể chạy độc lập hoặc tích hợp vào Celery worker.
"""
import json
import logging
import threading
from datetime import datetime
from typing import Optional

import psycopg2
import psycopg2.extras
import pika

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import (
    DB_URL, RABBITMQ_HOST, RABBITMQ_PORT,
    RABBITMQ_USER, RABBITMQ_PASS, RABBITMQ_QUEUE,
)
from etl.cleaner import clean_review, normalize_rating
from etl.nlp_processor import VietnameseNLPProcessor

logger = logging.getLogger(__name__)


class ETLPipeline:
    """
    Pipeline ETL: nhận đánh giá thô, xử lý, lưu vào DB.
    Thread-safe với connection pool.

    Sử dụng:
        pipeline = ETLPipeline()
        pipeline.process_and_save(review_raw)
        # Hoặc chạy worker lắng nghe queue:
        pipeline.run_worker()
    """

    def __init__(self):
        self._nlp = VietnameseNLPProcessor()
        self._db_conn: Optional[psycopg2.connection] = None
        self._lock = threading.Lock()
        self.stats = {"processed": 0, "saved": 0, "skipped": 0, "errors": 0}

    # ── Database ──────────────────────────────────────────────────────────────
    def _get_db(self) -> psycopg2.connection:
        """Lấy kết nối DB, tái sử dụng nếu còn mở."""
        if self._db_conn is None or self._db_conn.closed:
            self._db_conn = psycopg2.connect(DB_URL)
            logger.info("Đã kết nối PostgreSQL.")
        return self._db_conn

    def _save_review(self, review: dict):
        """
        Lưu một đánh giá đã xử lý vào bảng reviews.
        Dùng INSERT … ON CONFLICT DO NOTHING để tránh trùng lặp.
        """
        sql = """
        INSERT INTO reviews (
            review_id, product_id, user_id, rating,
            title, content, content_raw,
            created_at, helpful_count, images,
            sentiment, sentiment_score, tokens, topics,
            crawled_at
        ) VALUES (
            %(review_id)s, %(product_id)s, %(user_id)s, %(rating)s,
            %(title)s, %(content)s, %(content_raw)s,
            %(created_at)s, %(helpful_count)s, %(images)s,
            %(sentiment)s, %(sentiment_score)s, %(tokens)s, %(topics)s,
            %(crawled_at)s
        )
        ON CONFLICT (review_id) DO NOTHING;
        """

        params = {
            "review_id":       review.get("review_id"),
            "product_id":      review.get("product_id"),
            "user_id":         review.get("user_id"),
            "rating":          normalize_rating(review.get("rating")),
            "title":           review.get("title_clean", "")[:500],
            "content":         review.get("content_clean", ""),
            "content_raw":     review.get("content_raw", ""),
            "created_at":      review.get("created_at") or None,
            "helpful_count":   int(review.get("helpful_count", 0)),
            "images":          review.get("images", []),
            "sentiment":       review.get("sentiment"),
            "sentiment_score": review.get("sentiment_score"),
            "tokens":          review.get("tokens", []),
            "topics":          review.get("topics", []),
            "crawled_at":      datetime.utcnow(),
        }

        with self._lock:
            conn = self._get_db()
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()

    # ── Transform ─────────────────────────────────────────────────────────────
    def transform(self, raw_review: dict) -> dict:
        """
        Áp dụng toàn bộ bước Transform:
          1. Làm sạch văn bản
          2. Xử lý NLP (tokenize, sentiment, topics)
        """
        # Bước 1: Làm sạch
        cleaned = clean_review(raw_review)

        # Bước 2: NLP
        processed = self._nlp.process_review(cleaned)

        return processed

    # ── Process & Save ────────────────────────────────────────────────────────
    def process_and_save(self, raw_review: dict) -> bool:
        """
        ETL hoàn chỉnh cho một đánh giá.
        Trả về True nếu thành công, False nếu lỗi.
        """
        if not raw_review.get("review_id"):
            logger.warning("Bỏ qua đánh giá không có review_id")
            self.stats["skipped"] += 1
            return False

        try:
            processed = self.transform(raw_review)
            self._save_review(processed)
            self.stats["processed"] += 1
            self.stats["saved"] += 1
            return True
        except Exception as e:
            logger.error(f"Lỗi ETL review {raw_review.get('review_id')}: {e}")
            self.stats["errors"] += 1
            return False

    # ── Batch processing từ file ──────────────────────────────────────────────
    def process_file(self, filepath: str) -> dict:
        """
        Xử lý hàng loạt từ file JSON (output của crawler).
        
        Args:
            filepath: Đường dẫn file JSON chứa list đánh giá

        Returns:
            Thống kê xử lý
        """
        logger.info(f"Đọc file: {filepath}")
        with open(filepath, encoding="utf-8") as f:
            reviews = json.load(f)

        logger.info(f"Bắt đầu xử lý {len(reviews)} đánh giá...")
        for i, review in enumerate(reviews):
            self.process_and_save(review)
            if (i + 1) % 1000 == 0:
                logger.info(f"  → Đã xử lý {i+1}/{len(reviews)} đánh giá")

        logger.info(f"Hoàn thành! Stats: {self.stats}")
        return self.stats

    # ── RabbitMQ Worker ───────────────────────────────────────────────────────
    def run_worker(self):
        """
        Chạy worker lắng nghe RabbitMQ và xử lý message liên tục.
        Chạy mãi cho đến khi bị dừng (Ctrl+C).
        """
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        params = pika.ConnectionParameters(
            host=RABBITMQ_HOST,
            port=RABBITMQ_PORT,
            credentials=credentials,
            heartbeat=600,
        )
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
        channel.basic_qos(prefetch_count=10)

        def callback(ch, method, properties, body):
            try:
                review = json.loads(body.decode("utf-8"))
                success = self.process_and_save(review)
                if success:
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                else:
                    # Nack và đẩy lại queue
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            except Exception as e:
                logger.error(f"Lỗi xử lý message: {e}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

        channel.basic_consume(queue=RABBITMQ_QUEUE, on_message_callback=callback)
        logger.info(f"Worker đang lắng nghe queue '{RABBITMQ_QUEUE}'... (Ctrl+C để dừng)")

        try:
            channel.start_consuming()
        except KeyboardInterrupt:
            channel.stop_consuming()
            connection.close()
            logger.info(f"Worker dừng. Stats: {self.stats}")

    # ── Train LDA trên toàn bộ corpus ────────────────────────────────────────
    def train_topic_model(self, save_path: str = "models/lda_model"):
        """
        Huấn luyện LDA trên toàn bộ corpus đã lưu trong DB.
        Cần chạy sau khi đã có đủ dữ liệu (~10k+ đánh giá).
        """
        logger.info("Đọc corpus từ database để train LDA...")
        conn = self._get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT tokens FROM reviews WHERE tokens IS NOT NULL AND array_length(tokens, 1) > 2")
            rows = cur.fetchall()

        corpus = [row[0] for row in rows if row[0]]
        if not corpus:
            logger.warning("Không có dữ liệu tokens trong DB.")
            return

        logger.info(f"Train LDA với {len(corpus)} tài liệu...")
        self._nlp.train_lda(corpus)

        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        self._nlp.save_lda(save_path)
        logger.info(f"Đã lưu LDA model → {save_path}")
