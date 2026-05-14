"""
tests/test_crawler.py
Unit test cho Tiki Crawler.
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from crawler.tiki_crawler import TikiCrawler
from crawler.proxy_manager import ProxyManager


class TestTikiCrawlerParser:
    """Test parsing đánh giá — không cần kết nối mạng."""

    def test_parse_review_basic(self):
        raw = {
            "id": "99999",
            "rating": 4,
            "title": "Sản phẩm ổn",
            "content": "Giao hàng nhanh",
            "thank_count": 5,
            "created_by": {"id": "user001"},
            "images": [],
            "status": "approved",
        }
        result = TikiCrawler._parse_review(raw, product_id="P001")
        assert result["review_id"] == "99999"
        assert result["product_id"] == "P001"
        assert result["rating"] == 4
        assert result["helpful_count"] == 5
        assert result["sentiment"] is None  # Chưa xử lý NLP

    def test_parse_review_missing_fields(self):
        """Đánh giá thiếu trường không được crash."""
        raw = {"id": "88888"}
        result = TikiCrawler._parse_review(raw, product_id="P002")
        assert result["review_id"] == "88888"
        assert result["rating"] == 0
        assert result["helpful_count"] == 0

    def test_parse_review_no_pii(self):
        """Không được lưu tên thật / PII."""
        raw = {
            "id": "77777",
            "created_by": {
                "id": "user123",
                "name": "Nguyễn Văn A",      # Không được lưu
                "email": "user@email.com",    # Không được lưu
            },
        }
        result = TikiCrawler._parse_review(raw, "P003")
        # Chỉ lưu ID ẩn danh
        assert result["user_id"] == "user123"
        # Không có trường name hoặc email
        assert "name" not in result
        assert "email" not in result


class TestCrawlerStats:
    def test_initial_stats(self):
        crawler = TikiCrawler(use_queue=False)
        assert crawler.stats["success"] == 0
        assert crawler.stats["failed"] == 0
        assert crawler.stats["total_reviews"] == 0

    def test_repr(self):
        crawler = TikiCrawler(use_queue=False)
        assert "TikiCrawler" in repr(crawler)


class TestProxyManager:
    def test_empty_proxy_manager(self):
        pm = ProxyManager([])
        assert pm.get_proxy() is None
        assert pm.total_count == 0

    def test_add_proxy(self):
        pm = ProxyManager(["http://proxy1:8080", "http://proxy2:8080"])
        assert pm.total_count == 2
        assert pm.active_count == 2

    def test_get_proxy_returns_active(self):
        pm = ProxyManager(["http://proxy1:8080"])
        proxy = pm.get_proxy()
        assert proxy == "http://proxy1:8080"

    def test_report_failure_deactivates(self):
        pm = ProxyManager(["http://bad-proxy:8080"])
        # Báo lỗi 3 lần -> vô hiệu hóa
        for _ in range(3):
            pm.report_failure("http://bad-proxy:8080")
        assert pm.active_count == 0

    def test_status(self):
        pm = ProxyManager(["http://p1:8080", "http://p2:8080"])
        status = pm.status()
        assert status["total"] == 2
        assert status["active"] == 2


@pytest.mark.asyncio
async def test_crawler_close():
    """Đảm bảo close() không gây lỗi."""
    crawler = TikiCrawler(use_queue=False)
    await crawler.close()  # Không crash khi chưa có session


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
