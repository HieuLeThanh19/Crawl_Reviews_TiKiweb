-- scripts/setup_db.sql
-- Schema PostgreSQL cho hệ thống thu thập đánh giá Tiki
-- Chạy: psql -U postgres -f scripts/setup_db.sql

-- Tạo database
CREATE DATABASE tiki_reviews
    ENCODING 'UTF8'
    LC_COLLATE 'en_US.UTF-8'
    LC_CTYPE 'en_US.UTF-8';

\c tiki_reviews;

-- ─── Bảng sản phẩm ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS products (
    product_id   VARCHAR(50) PRIMARY KEY,
    name         TEXT,
    category     VARCHAR(200),
    price        NUMERIC(15, 2),
    url          TEXT,
    created_at   TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP DEFAULT NOW()
);

-- ─── Bảng đánh giá chính ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reviews (
    -- Định danh
    review_id        VARCHAR(50)  PRIMARY KEY,
    product_id       VARCHAR(50)  NOT NULL,
    user_id          VARCHAR(50),               -- Mã ẩn danh, không lưu tên thật (GDPR)

    -- Nội dung đánh giá
    rating           SMALLINT     CHECK (rating BETWEEN 1 AND 5),
    title            VARCHAR(500),
    content          TEXT,                      -- Nội dung đã làm sạch
    content_raw      TEXT,                      -- Nội dung gốc (backup)

    -- Metadata
    created_at       TIMESTAMP,                 -- Thời gian đánh giá (từ Tiki)
    helpful_count    INTEGER      DEFAULT 0,
    images           TEXT[],                    -- Danh sách URL ảnh

    -- Kết quả NLP
    sentiment        VARCHAR(20)  CHECK (sentiment IN ('positive', 'negative', 'neutral')),
    sentiment_score  FLOAT        CHECK (sentiment_score BETWEEN 0 AND 1),
    tokens           TEXT[],                    -- Mảng token sau tách từ
    topics           TEXT[],                    -- Chủ đề phát hiện từ LDA

    -- Metadata thu thập
    crawled_at       TIMESTAMP    DEFAULT NOW(),
    etl_version      VARCHAR(20)  DEFAULT '1.0',

    -- Liên kết với sản phẩm (tuỳ chọn FK)
    CONSTRAINT fk_product FOREIGN KEY (product_id)
        REFERENCES products(product_id) ON DELETE CASCADE
);

-- ─── Indexes để tối ưu truy vấn ─────────────────────────────────────────────
-- Index theo sản phẩm (truy vấn thường xuyên nhất)
CREATE INDEX idx_reviews_product_id   ON reviews(product_id);

-- Index theo thời gian (lọc theo ngày)
CREATE INDEX idx_reviews_created_at   ON reviews(created_at DESC);

-- Index theo rating (lọc theo số sao)
CREATE INDEX idx_reviews_rating       ON reviews(rating);

-- Index theo sentiment (thống kê cảm xúc)
CREATE INDEX idx_reviews_sentiment    ON reviews(sentiment);

-- Composite index: product + rating (truy vấn phổ biến)
CREATE INDEX idx_reviews_product_rating ON reviews(product_id, rating);

-- GIN index cho array tokens (tìm kiếm từ trong token)
CREATE INDEX idx_reviews_tokens       ON reviews USING GIN(tokens);

-- GIN index cho array topics
CREATE INDEX idx_reviews_topics       ON reviews USING GIN(topics);

-- ─── View thống kê nhanh ────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_product_stats AS
SELECT
    product_id,
    COUNT(*)                                        AS total_reviews,
    ROUND(AVG(rating), 2)                           AS avg_rating,
    SUM(CASE WHEN sentiment = 'positive' THEN 1 ELSE 0 END) AS positive_count,
    SUM(CASE WHEN sentiment = 'negative' THEN 1 ELSE 0 END) AS negative_count,
    SUM(CASE WHEN sentiment = 'neutral'  THEN 1 ELSE 0 END) AS neutral_count,
    MAX(crawled_at)                                 AS last_crawled
FROM reviews
GROUP BY product_id;

-- ─── Bảng theo dõi trạng thái crawl ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS crawl_jobs (
    job_id       SERIAL       PRIMARY KEY,
    product_id   VARCHAR(50)  NOT NULL,
    status       VARCHAR(20)  DEFAULT 'pending'   -- pending | running | done | failed
                             CHECK (status IN ('pending', 'running', 'done', 'failed')),
    pages_done   INTEGER      DEFAULT 0,
    reviews_done INTEGER      DEFAULT 0,
    started_at   TIMESTAMP,
    finished_at  TIMESTAMP,
    error_msg    TEXT,
    created_at   TIMESTAMP    DEFAULT NOW()
);

-- ─── Dữ liệu mẫu để test ────────────────────────────────────────────────────
INSERT INTO products (product_id, name, category, url) VALUES
    ('ABC001', 'Điện thoại Samsung Galaxy A55', 'Điện thoại', 'https://tiki.vn/p/abc001'),
    ('ABC002', 'Laptop Asus VivoBook 15', 'Laptop', 'https://tiki.vn/p/abc002'),
    ('ABC003', 'Tai nghe Sony WH-1000XM5', 'Phụ kiện âm thanh', 'https://tiki.vn/p/abc003')
ON CONFLICT (product_id) DO NOTHING;

-- ─── Cấp quyền cho user ứng dụng ────────────────────────────────────────────
-- (Thay 'app_user' bằng user thực tế trong production)
-- GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO app_user;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;

SELECT 'Schema tiki_reviews đã được tạo thành công!' AS message;
