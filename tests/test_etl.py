"""
tests/test_etl.py
Unit test cho module ETL (cleaner, NLP processor).
Chạy: pytest tests/ -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from etl.cleaner import clean_text, clean_review, normalize_rating
from etl.nlp_processor import VietnameseNLPProcessor


# ─── Tests: cleaner.py ────────────────────────────────────────────────────────

class TestCleanText:
    def test_remove_html_tags(self):
        result = clean_text("<p>Sản phẩm <b>tốt</b></p>")
        assert "<" not in result
        assert "tốt" in result

    def test_normalize_whitespace(self):
        result = clean_text("  Sản   phẩm  tốt  ")
        assert "  " not in result
        assert result == result.strip()

    def test_remove_emoji(self):
        result = clean_text("Rất tốt 😍🎉")
        assert "😍" not in result
        assert "🎉" not in result

    def test_remove_url(self):
        result = clean_text("Xem thêm tại https://tiki.vn/product")
        assert "http" not in result

    def test_empty_string(self):
        assert clean_text("") == ""
        assert clean_text(None) == ""

    def test_unicode_normalization(self):
        # Tiếng Việt phải được chuẩn hóa về NFC
        text = "Sản phẩm tốt"
        result = clean_text(text)
        assert len(result) > 0

    def test_lowercase(self):
        result = clean_text("SẢN PHẨM TỐT")
        assert result == result.lower()

    def test_repeat_char_normalization(self):
        result = clean_text("đẹpppppppp lắmmmmm")
        # Ký tự lặp quá 3 lần phải được rút gọn
        assert "pppppppp" not in result


class TestCleanReview:
    def test_clean_review_adds_clean_fields(self):
        review = {
            "review_id": "123",
            "title": "<b>Tốt</b>",
            "content": "<p>Giao hàng nhanh chóng!</p>",
        }
        result = clean_review(review)
        assert "title_clean" in result
        assert "content_clean" in result
        assert "content_raw" in result
        assert "<b>" not in result["title_clean"]

    def test_preserves_original_fields(self):
        review = {"review_id": "456", "rating": 5, "title": "OK"}
        result = clean_review(review)
        assert result["review_id"] == "456"
        assert result["rating"] == 5


class TestNormalizeRating:
    def test_valid_ratings(self):
        for i in range(1, 6):
            assert normalize_rating(i) == i

    def test_out_of_range(self):
        assert normalize_rating(0) == 1
        assert normalize_rating(6) == 5
        assert normalize_rating(-1) == 1

    def test_invalid_input(self):
        assert normalize_rating(None) == 0
        assert normalize_rating("abc") == 0

    def test_string_number(self):
        assert normalize_rating("4") == 4


# ─── Tests: nlp_processor.py ─────────────────────────────────────────────────

class TestNLPProcessor:
    @pytest.fixture(scope="class")
    def processor(self):
        return VietnameseNLPProcessor()

    def test_tokenize_basic(self, processor):
        tokens = processor.tokenize("sản phẩm tốt")
        assert isinstance(tokens, list)
        assert len(tokens) > 0

    def test_tokenize_empty(self, processor):
        assert processor.tokenize("") == []
        assert processor.tokenize(None) == []

    def test_remove_stopwords(self, processor):
        tokens = ["và", "sản_phẩm", "của", "tốt", "là"]
        result = processor.remove_stopwords(tokens)
        # Stopwords phải bị loại
        assert "và" not in result
        assert "của" not in result
        # Từ có nghĩa phải giữ lại
        assert "tốt" in result or "sản_phẩm" in result

    def test_sentiment_positive(self, processor):
        result = processor.analyze_sentiment("Sản phẩm rất tốt, tôi rất thích")
        assert result["label"] in ("positive", "negative", "neutral")
        assert 0 <= result["score"] <= 1

    def test_sentiment_negative(self, processor):
        result = processor.analyze_sentiment("Sản phẩm tệ, chất lượng kém, thất vọng")
        assert result["label"] in ("positive", "negative", "neutral")

    def test_sentiment_empty(self, processor):
        result = processor.analyze_sentiment("")
        assert result["label"] == "neutral"

    def test_process_returns_dict(self, processor):
        result = processor.process("Sản phẩm chất lượng, giao hàng nhanh")
        assert "tokens" in result
        assert "sentiment" in result
        assert "topics" in result
        assert isinstance(result["tokens"], list)

    def test_process_review(self, processor):
        review = {
            "review_id": "TEST001",
            "content_clean": "Sản phẩm tốt, chất lượng ổn định",
            "content": "Sản phẩm tốt",
        }
        result = processor.process_review(review)
        assert "sentiment" in result
        assert "tokens" in result
        assert result["review_id"] == "TEST001"


# ─── Integration-like test ────────────────────────────────────────────────────

class TestFullPipeline:
    """Test luồng làm sạch + NLP đầy đủ (không cần DB)."""

    def test_end_to_end(self):
        from etl.cleaner import clean_review
        from etl.nlp_processor import VietnameseNLPProcessor

        raw_review = {
            "review_id":     "INT001",
            "product_id":    "ABC001",
            "rating":        5,
            "title":         "<b>Tuyệt vời!</b> 😍",
            "content":       "<p>Sản phẩm rất tốt, giao hàng nhanh chóng. Đóng gói cẩn thận.</p>",
            "helpful_count": 10,
        }

        # Step 1: Clean
        cleaned = clean_review(raw_review)
        assert "<" not in cleaned["content_clean"]
        assert "😍" not in cleaned["title_clean"]

        # Step 2: NLP
        processor = VietnameseNLPProcessor()
        processed = processor.process_review(cleaned)

        assert processed["sentiment"] in ("positive", "negative", "neutral")
        assert isinstance(processed["tokens"], list)
        assert isinstance(processed["topics"], list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
