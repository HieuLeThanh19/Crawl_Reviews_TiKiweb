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
from crawler.tiki_client import TikiClient, _is_relevant_product_match, _product_match_score, _raw_review_matches_selected
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


class FakeResponse:
    encoding = "utf-8"

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.headers = {}
        self.trust_env = False

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}, "timeout": timeout})
        return FakeResponse(self.payload)


class TestTikiClientReviews:
    def test_get_product_detail_normalizes_review_count_and_spid(self):
        client = TikiClient()
        client.session = FakeSession(
            {
                "id": "P001",
                "name": "San pham that",
                "seller_product_id": "S001",
                "review_count": 42,
                "rating_average": 4.8,
                "url_path": "san-pham-that-pP001.html?spid=S001",
            }
        )

        result = client.get_product_detail("P001", spid="S001")

        assert client.session.calls[0]["params"]["spid"] == "S001"
        assert result["product_id"] == "P001"
        assert result["spid"] == "S001"
        assert result["review_count"] == 42

    def test_fetch_reviews_passes_spid_to_tiki_api(self):
        client = TikiClient()
        client.session = FakeSession(
            {
                "reviews_count": 1,
                "rating_average": 5,
                "paging": {"last_page": 1, "total": 1},
                "data": [{"id": "R1", "rating": 5, "product_id": "P001", "spid": "S001"}],
            }
        )

        result = client.fetch_reviews("P001", max_pages=1, spid="S001")

        assert client.session.calls[0]["params"]["spid"] == "S001"
        assert result["summary"]["filtered_mismatch_count"] == 0
        assert len(result["reviews"]) == 1

    def test_raw_review_mismatch_is_rejected_when_tiki_marks_another_listing(self):
        assert _raw_review_matches_selected(
            {"product_id": "P002", "spid": "S001"},
            product_id="P001",
            spid="S001",
        )
        assert not _raw_review_matches_selected(
            {"product_id": "P001", "spid": "S002"},
            product_id="P001",
            spid="S001",
        )
        assert _raw_review_matches_selected(
            {"id": "R1", "rating": 5},
            product_id="P001",
            spid="S001",
        )

    def test_phone_search_prefers_name_match_before_review_count(self):
        phone = {
            "name": "Điện thoại Samsung Galaxy A56 5G",
            "brand": "Samsung",
            "review_count": 120,
            "rating_average": 4.7,
        }
        rice_cooker = {
            "name": "Nồi Cơm Điện Mini Lock&Lock 0.8 lít",
            "brand": "LocknLock",
            "review_count": 312,
            "rating_average": 4.8,
        }

        assert _is_relevant_product_match("điện thoại", phone, "phones")
        assert not _is_relevant_product_match("điện thoại", rice_cooker, "phones")
        assert _product_match_score("điện thoại", phone, "phones") > _product_match_score("điện thoại", rice_cooker, "phones")

    def test_clothing_search_does_not_match_the_thao_substring(self):
        shoe = {
            "name": "Giày thể thao nữ đế êm nhẹ, thoáng khí",
            "brand": "Bee Gee",
            "review_count": 6,
            "rating_average": 5,
        }
        shirt = {
            "name": "Áo Thể Thao Nữ Basic V Neck",
            "brand": "Just Feel Free",
            "review_count": 5,
            "rating_average": 5,
        }

        assert not _is_relevant_product_match("áo nữ", shoe, "")
        assert _is_relevant_product_match("áo nữ", shirt, "")
        assert _product_match_score("áo nữ", shirt, "") > _product_match_score("áo nữ", shoe, "")


@pytest.mark.asyncio
async def test_crawler_close():
    """Đảm bảo close() không gây lỗi."""
    crawler = TikiCrawler(use_queue=False)
    await crawler.close()  # Không crash khi chưa có session


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
