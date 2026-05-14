"""
crawler/tiki_crawler.py
Crawler bất đồng bộ thu thập đánh giá sản phẩm từ Tiki.vn.

Chiến lược:
  - Dùng aiohttp để gửi request bất đồng bộ, tăng throughput
  - Giới hạn CRAWL_DELAY giữa request để tránh bị block
  - Retry với exponential backoff khi gặp lỗi mạng / 429
  - Đẩy kết quả vào RabbitMQ để worker ETL xử lý

Lưu ý tuân thủ pháp lý:
  - Không truy cập /api/v2/reviews/writable (bị cấm trong robots.txt)
  - Tuân thủ delay tối thiểu 1 giây giữa request
  - Chỉ thu thập đánh giá công khai
"""
import asyncio
import json
import logging
import time
from typing import AsyncGenerator, Optional

import aiohttp
import pika
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import (
    CRAWL_DELAY, DEFAULT_HEADERS, MAX_CONCURRENT,
    MAX_RETRIES, RABBITMQ_HOST, RABBITMQ_PORT,
    RABBITMQ_USER, RABBITMQ_PASS, RABBITMQ_QUEUE,
    REQUEST_TIMEOUT, REVIEWS_PER_PAGE,
)

logger = logging.getLogger(__name__)

# ─── Endpoint Tiki (có thể thay đổi theo thực tế) ────────────────────────────
# Endpoint này là endpoint công khai (không phải /writable bị cấm)
TIKI_REVIEW_API = "https://tiki.vn/api/v2/reviews"


class TikiCrawler:
    """
    Crawler bất đồng bộ thu thập đánh giá sản phẩm từ Tiki.
    
    Sử dụng:
        crawler = TikiCrawler()
        reviews = await crawler.crawl_product("123456", max_pages=50)
        await crawler.close()
    """

    def __init__(self, use_queue: bool = True):
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self._mq_connection = None
        self._mq_channel = None
        self.use_queue = use_queue
        self.stats = {"success": 0, "failed": 0, "total_reviews": 0}

    # ── Khởi tạo session ──────────────────────────────────────────────────────
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            self._session = aiohttp.ClientSession(
                headers=DEFAULT_HEADERS,
                timeout=timeout,
            )
        return self._session

    # ── Kết nối RabbitMQ ──────────────────────────────────────────────────────
    def _connect_rabbitmq(self):
        """Kết nối đến RabbitMQ và tạo channel."""
        try:
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
            params = pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials,
                heartbeat=600,
            )
            self._mq_connection = pika.BlockingConnection(params)
            self._mq_channel = self._mq_connection.channel()
            self._mq_channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
            logger.info("Đã kết nối RabbitMQ thành công.")
        except Exception as e:
            logger.warning(f"Không kết nối được RabbitMQ: {e}. Sẽ xử lý trực tiếp.")
            self.use_queue = False

    # ── Gửi message vào queue ────────────────────────────────────────────────
    def _publish_review(self, review: dict):
        """Đẩy đánh giá vào hàng đợi RabbitMQ."""
        if not self._mq_channel:
            return
        try:
            self._mq_channel.basic_publish(
                exchange="",
                routing_key=RABBITMQ_QUEUE,
                body=json.dumps(review, ensure_ascii=False),
                properties=pika.BasicProperties(delivery_mode=2),  # persistent
            )
        except Exception as e:
            logger.error(f"Lỗi khi đẩy vào queue: {e}")

    # ── Fetch một trang đánh giá ─────────────────────────────────────────────
    @retry(
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=False,
    )
    async def _fetch_page(
        self,
        session: aiohttp.ClientSession,
        product_id: str,
        page: int,
    ) -> Optional[dict]:
        """
        Lấy một trang đánh giá từ Tiki API.
        Trả về dict JSON nếu thành công, None nếu thất bại.
        """
        params = {
            "product_id": product_id,
            "limit": REVIEWS_PER_PAGE,
            "page": page,
            "sort": "score|desc,id|desc,stars|all",
        }
        async with self._semaphore:
            try:
                async with session.get(
                    TIKI_REVIEW_API, params=params
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        self.stats["success"] += 1
                        return data
                    elif resp.status == 429:
                        # Too Many Requests — đợi thêm
                        logger.warning(
                            f"429 Too Many Requests cho product {product_id} "
                            f"trang {page}. Đợi 10 giây..."
                        )
                        await asyncio.sleep(10)
                        return None
                    elif resp.status == 404:
                        logger.warning(f"Không tìm thấy product {product_id}")
                        return None
                    else:
                        logger.error(
                            f"HTTP {resp.status} cho product {product_id} trang {page}"
                        )
                        self.stats["failed"] += 1
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f"Lỗi mạng: {e}")
                raise  # tenacity sẽ retry
            finally:
                # Tuân thủ delay để không bị block
                await asyncio.sleep(CRAWL_DELAY)

    # ── Crawl một sản phẩm ────────────────────────────────────────────────────
    async def crawl_product(
        self,
        product_id: str,
        max_pages: int = 100,
    ) -> list[dict]:
        """
        Thu thập toàn bộ đánh giá của một sản phẩm.

        Args:
            product_id: ID sản phẩm trên Tiki
            max_pages:  Giới hạn số trang tối đa

        Returns:
            Danh sách các đánh giá đã thu thập
        """
        logger.info(f"Bắt đầu crawl sản phẩm {product_id} (tối đa {max_pages} trang)")
        session = await self._get_session()
        all_reviews = []

        for page in range(1, max_pages + 1):
            data = await self._fetch_page(session, product_id, page)

            if data is None:
                logger.info(f"Dừng tại trang {page} do lỗi hoặc hết dữ liệu")
                break

            reviews = data.get("data", [])
            if not reviews:
                logger.info(f"Hết đánh giá tại trang {page} (product {product_id})")
                break

            # Parse và chuẩn hóa mỗi đánh giá
            for raw in reviews:
                review = self._parse_review(raw, product_id)
                all_reviews.append(review)

                # Đẩy vào RabbitMQ nếu có kết nối
                if self.use_queue:
                    self._publish_review(review)

            self.stats["total_reviews"] += len(reviews)
            logger.info(
                f"Product {product_id}: đã thu được {len(all_reviews)} đánh giá "
                f"(trang {page}/{max_pages})"
            )

            # Kiểm tra còn trang tiếp theo không
            paging = data.get("paging", {})
            if page >= paging.get("last_page", 1):
                break

        return all_reviews

    # ── Crawl nhiều sản phẩm đồng thời ───────────────────────────────────────
    async def crawl_products(
        self,
        product_ids: list[str],
        max_pages: int = 100,
    ) -> dict[str, list]:
        """Thu thập đánh giá cho nhiều sản phẩm đồng thời."""
        tasks = [
            self.crawl_product(pid, max_pages) for pid in product_ids
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = {}
        for pid, result in zip(product_ids, results):
            if isinstance(result, Exception):
                logger.error(f"Lỗi crawl product {pid}: {result}")
                output[pid] = []
            else:
                output[pid] = result
        return output

    # ── Parse đánh giá từ JSON của Tiki ──────────────────────────────────────
    @staticmethod
    def _parse_review(raw: dict, product_id: str) -> dict:
       
        # Thông tin người dùng — chỉ lấy avatar_url công khai, không lấy PII
        created_by = raw.get("created_by", {}) or {}

        return {
            "review_id":     str(raw.get("id", "")),
            "product_id":    product_id,
            # Chỉ lưu mã ẩn danh (id), không lưu tên thật
            "user_id":       str(created_by.get("id", "")),
            "rating":        int(raw.get("rating", 0)),
            "title":         raw.get("title", ""),
            "content":       raw.get("content", ""),
            "created_at":    raw.get("created_at", ""),
            "helpful_count": int(raw.get("thank_count", 0)),
            "images":        [
                img.get("full_path", "")
                for img in (raw.get("images") or [])
            ],
            # Trường NLP sẽ được điền bởi ETL pipeline
            "sentiment":     None,
            "tokens":        [],
            "topics":        [],
            # Metadata thu thập
            "_crawled_at": time.time(),
            "_raw_status":   raw.get("status", ""),
        }

    # ── Đóng kết nối ─────────────────────────────────────────────────────────
    async def close(self):
        """Đóng session HTTP và kết nối RabbitMQ."""
        if self._session and not self._session.closed:
            await self._session.close()
        if self._mq_connection and not self._mq_connection.is_closed:
            self._mq_connection.close()

    def __repr__(self):
        return (
            f"TikiCrawler(success={self.stats['success']}, "
            f"failed={self.stats['failed']}, "
            f"total_reviews={self.stats['total_reviews']})"
        )


# ─── Entry point CLI ─────────────────────────────────────────────────────────
async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Crawl đánh giá sản phẩm Tiki")
    parser.add_argument("--product-id", required=True, help="ID sản phẩm Tiki")
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--no-queue", action="store_true", help="Không dùng RabbitMQ")
    parser.add_argument("--output", default="reviews_output.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    crawler = TikiCrawler(use_queue=not args.no_queue)
    if not args.no_queue:
        crawler._connect_rabbitmq()

    try:
        reviews = await crawler.crawl_product(args.product_id, args.max_pages)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(reviews, f, ensure_ascii=False, indent=2)
        print(f"\n✅ Hoàn thành! Thu được {len(reviews)} đánh giá → {args.output}")
        print(f"   Stats: {crawler}")
    finally:
        await crawler.close()


if __name__ == "__main__":
    asyncio.run(main())
