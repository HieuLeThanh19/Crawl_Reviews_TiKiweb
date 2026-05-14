"""
crawler/scrapy_spider.py
Spider Scrapy thu thập đánh giá Tiki — phương án thay thế aiohttp.

Cách chạy:
    scrapy runspider crawler/scrapy_spider.py -o reviews.json

Scrapy tự động quản lý concurrency, retry, throttle.
"""
import json
import time
from typing import Generator

import scrapy
from scrapy import Spider, Request
from scrapy.http import Response

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import CRAWL_DELAY, DEFAULT_HEADERS, MAX_RETRIES, REVIEWS_PER_PAGE


class TikiReviewSpider(Spider):
    """
    Scrapy Spider thu thập đánh giá sản phẩm từ Tiki API JSON.

    Cài đặt:
        scrapy settings: DOWNLOAD_DELAY, CONCURRENT_REQUESTS
    """
    name = "tiki_reviews"
    allowed_domains = ["tiki.vn"]

    # Cấu hình Scrapy
    custom_settings = {
        "DOWNLOAD_DELAY": CRAWL_DELAY,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "RETRY_TIMES": MAX_RETRIES,
        "RETRY_HTTP_CODES": [429, 500, 502, 503, 504],
        "DEFAULT_REQUEST_HEADERS": DEFAULT_HEADERS,
        "ROBOTSTXT_OBEY": True,          # Tuân thủ robots.txt
        "AUTOTHROTTLE_ENABLED": True,    # Tự động điều chỉnh tốc độ
        "AUTOTHROTTLE_START_DELAY": 1.0,
        "AUTOTHROTTLE_MAX_DELAY": 10.0,
        "LOG_LEVEL": "INFO",
        # Pipelines: có thể kết nối với ETLPipeline
        "ITEM_PIPELINES": {
            "crawler.scrapy_spider.TikiReviewPipeline": 300,
        },
    }

    def __init__(self, product_ids: str = "ABC001", max_pages: int = 100, **kwargs):
        """
        Args:
            product_ids: Chuỗi product ID cách nhau bởi dấu phẩy
            max_pages:   Số trang tối đa mỗi sản phẩm
        """
        super().__init__(**kwargs)
        self.product_ids = [pid.strip() for pid in product_ids.split(",")]
        self.max_pages = int(max_pages)
        self.stats_count = {pid: 0 for pid in self.product_ids}

    def start_requests(self) -> Generator[Request, None, None]:
        """Tạo request đầu tiên cho mỗi sản phẩm."""
        for product_id in self.product_ids:
            yield self._make_request(product_id, page=1)

    def _make_request(self, product_id: str, page: int) -> Request:
        """Tạo Request đến Tiki API."""
        url = (
            f"https://tiki.vn/api/v2/reviews"
            f"?product_id={product_id}"
            f"&limit={REVIEWS_PER_PAGE}"
            f"&page={page}"
            f"&sort=score|desc,id|desc,stars|all"
        )
        return Request(
            url=url,
            callback=self.parse_reviews,
            errback=self.handle_error,
            meta={"product_id": product_id, "page": page},
            dont_filter=False,
        )

    def parse_reviews(self, response: Response) -> Generator[dict, None, None]:
        """Parse response JSON và yield từng đánh giá."""
        meta = response.meta
        product_id = meta["product_id"]
        page = meta["page"]

        if response.status != 200:
            self.logger.warning(f"HTTP {response.status} — product {product_id} trang {page}")
            return

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError as e:
            self.logger.error(f"Lỗi parse JSON: {e}")
            return

        reviews = data.get("data", [])
        if not reviews:
            self.logger.info(f"Hết đánh giá: product {product_id} tại trang {page}")
            return

        # Yield từng đánh giá
        for raw in reviews:
            yield self._parse_review(raw, product_id)

        self.stats_count[product_id] += len(reviews)
        self.logger.info(
            f"Product {product_id}: trang {page} → {len(reviews)} reviews "
            f"(tổng: {self.stats_count[product_id]})"
        )

        # Tiếp tục trang tiếp theo nếu còn
        paging = data.get("paging", {})
        last_page = paging.get("last_page", 1)
        if page < min(last_page, self.max_pages):
            yield self._make_request(product_id, page + 1)

    def handle_error(self, failure):
        """Xử lý lỗi request (timeout, connection error...)."""
        self.logger.error(f"Request lỗi: {failure.value}")

    @staticmethod
    def _parse_review(raw: dict, product_id: str) -> dict:
        """Chuẩn hóa đánh giá thô từ Tiki API."""
        created_by = raw.get("created_by") or {}
        return {
            "review_id":     str(raw.get("id", "")),
            "product_id":    product_id,
            "user_id":       str(created_by.get("id", "")),  # Chỉ ID ẩn danh
            "rating":        int(raw.get("rating", 0)),
            "title":         raw.get("title", ""),
            "content":       raw.get("content", ""),
            "created_at":    raw.get("created_at", ""),
            "helpful_count": int(raw.get("thank_count", 0)),
            "images":        [img.get("full_path", "") for img in (raw.get("images") or [])],
            "sentiment":     None,
            "tokens":        [],
            "topics":        [],
            "_crawled_at":   time.time(),
        }


class TikiReviewPipeline:
    """
    Scrapy Item Pipeline: nhận review từ Spider, gửi vào ETL.
    Kết nối với ETLPipeline để xử lý NLP và lưu DB.
    """

    def open_spider(self, spider):
        """Khởi tạo pipeline khi spider bắt đầu."""
        try:
            from etl.pipeline import ETLPipeline
            self.etl = ETLPipeline()
            spider.logger.info("ETL Pipeline đã sẵn sàng.")
        except Exception as e:
            spider.logger.warning(f"Không khởi tạo được ETL Pipeline: {e}")
            self.etl = None

    def process_item(self, item: dict, spider) -> dict:
        """Xử lý ETL cho mỗi item."""
        if self.etl:
            try:
                self.etl.process_and_save(item)
            except Exception as e:
                spider.logger.error(f"Lỗi ETL item {item.get('review_id')}: {e}")
        return item

    def close_spider(self, spider):
        """Thống kê khi spider kết thúc."""
        if self.etl:
            spider.logger.info(f"ETL Stats: {self.etl.stats}")
