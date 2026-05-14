"""
etl/nlp_processor.py
Xử lý NLP tiếng Việt cho đánh giá sản phẩm Tiki.

Các tính năng:
  - Tokenization (tách từ) dùng Underthesea
  - Loại bỏ stopwords tiếng Việt
  - Phân tích cảm xúc (sentiment analysis)
  - Topic modeling với LDA (Gensim)
  - Embedding câu (tùy chọn, dùng PhoBERT)

Phụ thuộc:
  pip install underthesea gensim
"""
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Lazy imports — tránh lỗi nếu chưa cài thư viện ─────────────────────────
try:
    from underthesea import word_tokenize, sentiment
    _HAS_UNDERTHESEA = True
except ImportError:
    logger.warning("underthesea chưa được cài. Chạy: pip install underthesea")
    _HAS_UNDERTHESEA = False

try:
    from gensim import corpora
    from gensim.models import LdaModel
    _HAS_GENSIM = True
except ImportError:
    logger.warning("gensim chưa được cài. Topic modeling sẽ bị tắt.")
    _HAS_GENSIM = False


class VietnameseNLPProcessor:
    """
    Pipeline xử lý NLP tiếng Việt cho đánh giá sản phẩm.

    Sử dụng:
        processor = VietnameseNLPProcessor()
        result = processor.process("Sản phẩm rất tốt, giao hàng nhanh")
        # result = {"tokens": [...], "sentiment": "positive", "topics": [...]}
    """

    def __init__(
        self,
        stopwords_path: Optional[str] = None,
        lda_model_path: Optional[str] = None,
        num_topics: int = 10,
    ):
        # Load stopwords
        self._stopwords = self._load_stopwords(stopwords_path)
        logger.info(f"Đã nạp {len(self._stopwords)} stopwords tiếng Việt.")

        # LDA model (sẽ được huấn luyện hoặc load)
        self._lda_model: Optional[LdaModel] = None
        self._dictionary: Optional[corpora.Dictionary] = None
        self._num_topics = num_topics

        if lda_model_path and os.path.exists(lda_model_path) and _HAS_GENSIM:
            self._load_lda(lda_model_path)

    # ── Load stopwords ────────────────────────────────────────────────────────
    @staticmethod
    def _load_stopwords(path: Optional[str]) -> set:
        """Đọc danh sách stopwords từ file (mỗi dòng một từ)."""
        default_path = Path(__file__).parent.parent / "config" / "vietnamese_stopwords.txt"
        file_path = Path(path) if path else default_path

        if file_path.exists():
            with open(file_path, encoding="utf-8") as f:
                return {line.strip().lower() for line in f if line.strip()}
        else:
            logger.warning(f"Không tìm thấy file stopwords: {file_path}")
            # Stopwords cơ bản mặc định
            return {"và", "của", "là", "có", "không", "trong", "được",
                    "cho", "với", "này", "đó", "các", "để", "một"}

    # ── Tokenization ──────────────────────────────────────────────────────────
    def tokenize(self, text: str) -> list[str]:
        """
        Tách từ tiếng Việt dùng Underthesea.
        
        Args:
            text: Văn bản đã làm sạch
        
        Returns:
            Danh sách token (từ đơn và ghép)
        
        Ví dụ:
            >>> processor.tokenize("giao hàng nhanh chóng")
            ['giao_hàng', 'nhanh_chóng']
        """
        if not text:
            return []

        if _HAS_UNDERTHESEA:
            try:
                tokenized = word_tokenize(text, format="text")
                tokens = tokenized.split()
            except Exception as e:
                logger.error(f"Lỗi tokenize: {e}")
                tokens = text.split()
        else:
            # Fallback: tách theo khoảng trắng
            tokens = text.split()

        return tokens

    # ── Loại bỏ stopwords ────────────────────────────────────────────────────
    def remove_stopwords(self, tokens: list[str]) -> list[str]:
        """Loại bỏ stopwords và token quá ngắn (< 2 ký tự)."""
        return [
            t for t in tokens
            if t.lower() not in self._stopwords and len(t) >= 2
        ]

    # ── Phân tích cảm xúc ────────────────────────────────────────────────────
    def analyze_sentiment(self, text: str) -> dict:
        """
        Phân tích cảm xúc tiếng Việt dùng Underthesea.

        Returns:
            {
                "label": "positive" | "negative" | "neutral",
                "score": float (nếu có)
            }
        """
        if not text:
            return {"label": "neutral", "score": 0.5}

        if _HAS_UNDERTHESEA:
            try:
                result = sentiment(text)
                # Underthesea có thể trả về str hoặc tuple
                if isinstance(result, (list, tuple)):
                    label = str(result[0]).lower()
                    score = float(result[1]) if len(result) > 1 else 0.5
                else:
                    label = str(result).lower()
                    score = 1.0 if label == "positive" else 0.0

                # Chuẩn hóa nhãn
                if "pos" in label:
                    label = "positive"
                elif "neg" in label:
                    label = "negative"
                else:
                    label = "neutral"

                return {"label": label, "score": score}

            except Exception as e:
                logger.error(f"Lỗi phân tích cảm xúc: {e}")

        # Fallback: phân tích đơn giản dựa trên từ khóa
        return self._rule_based_sentiment(text)

    def _rule_based_sentiment(self, text: str) -> dict:
        """Phân tích cảm xúc dựa trên từ điển — dùng khi không có Underthesea."""
        positive_words = {
            "tốt", "ngon", "đẹp", "nhanh", "ổn", "tuyệt", "hài lòng",
            "chất lượng", "xuất sắc", "thích", "hay", "ok", "oke", "ok",
            "đáng", "xịn", "chuẩn", "đúng", "hợp lý"
        }
        negative_words = {
            "tệ", "xấu", "chậm", "lỗi", "hỏng", "kém", "thất vọng",
            "không tốt", "đắt", "giả", "nhái", "vỡ", "sai", "thiếu"
        }

        text_lower = text.lower()
        pos_count = sum(1 for w in positive_words if w in text_lower)
        neg_count = sum(1 for w in negative_words if w in text_lower)

        if pos_count > neg_count:
            return {"label": "positive", "score": 0.7}
        elif neg_count > pos_count:
            return {"label": "negative", "score": 0.3}
        else:
            return {"label": "neutral", "score": 0.5}

    # ── Topic Modeling ────────────────────────────────────────────────────────
    def train_lda(self, corpus_tokens: list[list[str]]):
        """
        Huấn luyện mô hình LDA trên corpus.
        
        Args:
            corpus_tokens: Danh sách các đánh giá, mỗi đánh giá là list token
        """
        if not _HAS_GENSIM:
            logger.warning("Cần cài gensim để dùng topic modeling.")
            return

        logger.info(f"Huấn luyện LDA với {len(corpus_tokens)} tài liệu, {self._num_topics} chủ đề...")
        self._dictionary = corpora.Dictionary(corpus_tokens)
        self._dictionary.filter_extremes(no_below=5, no_above=0.5)

        bow_corpus = [self._dictionary.doc2bow(tokens) for tokens in corpus_tokens]

        self._lda_model = LdaModel(
            corpus=bow_corpus,
            id2word=self._dictionary,
            num_topics=self._num_topics,
            random_state=42,
            passes=10,
            alpha="auto",
        )
        logger.info("Huấn luyện LDA hoàn thành.")

    def get_topics(self, tokens: list[str], top_n: int = 3) -> list[str]:
        """
        Lấy top N chủ đề cho một đánh giá.
        
        Returns:
            Danh sách từ khóa chủ đề
        """
        if not self._lda_model or not self._dictionary or not tokens:
            return []

        try:
            bow = self._dictionary.doc2bow(tokens)
            topic_distribution = self._lda_model.get_document_topics(bow)
            # Sắp xếp theo xác suất giảm dần
            topic_distribution.sort(key=lambda x: x[1], reverse=True)

            topics = []
            for topic_id, _ in topic_distribution[:top_n]:
                top_words = self._lda_model.show_topic(topic_id, topn=3)
                topics.extend([word for word, _ in top_words])
            return list(dict.fromkeys(topics))  # loại trùng, giữ thứ tự
        except Exception as e:
            logger.error(f"Lỗi get_topics: {e}")
            return []

    def save_lda(self, path: str):
        """Lưu mô hình LDA ra file."""
        if self._lda_model:
            self._lda_model.save(path)
            self._dictionary.save(path + ".dict")
            logger.info(f"Đã lưu LDA model → {path}")

    def _load_lda(self, path: str):
        """Tải mô hình LDA đã huấn luyện."""
        try:
            self._lda_model = LdaModel.load(path)
            self._dictionary = corpora.Dictionary.load(path + ".dict")
            logger.info(f"Đã tải LDA model từ {path}")
        except Exception as e:
            logger.error(f"Lỗi tải LDA: {e}")

    # ── Xử lý toàn bộ ────────────────────────────────────────────────────────
    def process(self, text: str) -> dict:
        """
        Chạy toàn bộ pipeline NLP cho một đoạn văn bản.
        
        Returns:
            {
                "tokens": list[str],
                "sentiment": {"label": str, "score": float},
                "topics": list[str]
            }
        
        Ví dụ:
            >>> result = processor.process("Sản phẩm tốt, giao hàng nhanh chóng")
            >>> result["sentiment"]["label"]
            'positive'
        """
        tokens_raw    = self.tokenize(text)
        tokens_clean  = self.remove_stopwords(tokens_raw)
        sentiment_res = self.analyze_sentiment(text)
        topics        = self.get_topics(tokens_clean)

        return {
            "tokens":    tokens_clean,
            "sentiment": sentiment_res,
            "topics":    topics,
        }

    def process_review(self, review: dict) -> dict:
        """
        Áp dụng pipeline NLP lên một đánh giá đã làm sạch.
        Điền vào các trường: tokens, sentiment, topics.
        """
        text = review.get("content_clean") or review.get("content", "")
        result = self.process(text)

        updated = review.copy()
        updated["tokens"]    = result["tokens"]
        updated["sentiment"] = result["sentiment"]["label"]
        updated["sentiment_score"] = result["sentiment"]["score"]
        updated["topics"]    = result["topics"]
        return updated
