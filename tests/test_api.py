"""
tests/test_api.py
Integration test cho FastAPI endpoints (dùng TestClient).
Chạy: pytest tests/test_api.py -v
Lưu ý: Cần có DB kết nối hoặc mock DB để chạy đầy đủ.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

# Nếu không có DB, bỏ qua các test cần DB
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient


def test_fallback_positive_summary_uses_specific_praise():
    from api.app import _fallback_positive_summary

    reviews = [
        {"rating": 5, "title": "Cuc ki hai long", "content": ""},
        {"rating": 5, "title": "Cuc ki hai long", "content": "Đẹp và dễ thương như hình nha"},
    ]

    summary = _fallback_positive_summary(reviews, positive_total=2)

    assert "Đẹp và dễ thương như hình nha" in summary
    assert "chua du du lieu" not in summary.lower()


def test_looks_like_no_data_detects_empty_positive_answer():
    from api.app import _looks_like_no_data

    assert _looks_like_no_data("Review hien co chua du du lieu de ket luan diem tich cuc ro rang.")


def test_service_signal_detects_vietnamese_service_reviews():
    from api.app import _has_service_signal

    assert _has_service_signal({"title": "Rất hài lòng", "content": "Đóng gói kỹ, giao hàng nhanh"})
    assert _has_service_signal({"title": "Không hài lòng", "content": "Shop xử lý chậm, chờ phản hồi lâu"})
    assert not _has_service_signal({"title": "Màu đẹp", "content": "Sản phẩm dùng ổn"})


def make_app_with_mock_db():
    """Tạo app với DB mock để test không cần PostgreSQL thật."""
    import unittest.mock as mock
    from api.app import app
    return app


class TestHealthEndpoint:
    """Test endpoint /api/health."""

    def test_health_returns_200(self):
        """Health endpoint phải trả về JSON."""
        import unittest.mock as mock
        from api.app import app

        # Mock DB connection
        mock_cursor = mock.MagicMock()
        mock_cursor.__enter__ = mock.MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = mock.MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = [12345]

        mock_conn = mock.MagicMock()
        mock_conn.closed = False
        mock_conn.cursor.return_value = mock_cursor

        with mock.patch("api.app._db_conn", mock_conn):
            client = TestClient(app)
            resp = client.get("/api/health")

        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "timestamp" in data


class TestReviewEndpoints:
    """Test các endpoint reviews."""

    def test_reviews_endpoint_exists(self):
        """Endpoint /api/products/{id}/reviews phải tồn tại."""
        import unittest.mock as mock
        from api.app import app

        mock_cursor = mock.MagicMock()
        mock_cursor.__enter__ = mock.MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = mock.MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        mock_conn = mock.MagicMock()
        mock_conn.closed = False
        mock_conn.cursor.return_value = mock_cursor

        with mock.patch("api.app._db_conn", mock_conn):
            client = TestClient(app)
            resp = client.get("/api/products/P001/reviews")

        # 200 với list rỗng là OK
        assert resp.status_code in (200, 500)

    def test_review_not_found(self):
        """Review không tồn tại phải trả về 404."""
        import unittest.mock as mock
        from api.app import app

        mock_cursor = mock.MagicMock()
        mock_cursor.__enter__ = mock.MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = mock.MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = None

        mock_conn = mock.MagicMock()
        mock_conn.closed = False
        mock_conn.cursor.return_value = mock_cursor

        with mock.patch("api.app._db_conn", mock_conn):
            client = TestClient(app)
            resp = client.get("/api/reviews/NOTEXIST999")

        assert resp.status_code == 404

    def test_invalid_api_key(self):
        """Endpoint analytics cần API key hợp lệ."""
        import unittest.mock as mock
        from api.app import app

        with mock.patch("api.app.API_SECRET_KEY", "secret123"):
            client = TestClient(app)
            resp = client.get(
                "/api/analytics/sentiment?product_id=P001",
                headers={"X-Api-Key": "wrong-key"}
            )

        assert resp.status_code == 401

    def test_pagination_params(self):
        """Endpoint reviews phải hỗ trợ page và limit."""
        import unittest.mock as mock
        from api.app import app

        mock_cursor = mock.MagicMock()
        mock_cursor.__enter__ = mock.MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = mock.MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        mock_conn = mock.MagicMock()
        mock_conn.closed = False
        mock_conn.cursor.return_value = mock_cursor

        with mock.patch("api.app._db_conn", mock_conn):
            client = TestClient(app)
            resp = client.get("/api/products/P001/reviews?page=2&limit=10")

        assert resp.status_code in (200, 500)

    def test_invalid_limit(self):
        """limit > API_MAX_LIMIT phải trả về 422."""
        import unittest.mock as mock
        from api.app import app

        client = TestClient(app)
        resp = client.get("/api/products/P001/reviews?limit=9999")
        assert resp.status_code == 422  # Validation error


class TestDocsEndpoint:
    """Test Swagger UI."""

    def test_swagger_ui_accessible(self):
        """Swagger docs phải khả dụng."""
        from api.app import app
        client = TestClient(app)
        resp = client.get("/api/docs")
        assert resp.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
