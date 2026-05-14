#!/usr/bin/env python3
"""
scripts/seed_demo_data.py
Tạo dữ liệu mẫu (100 đánh giá) vào PostgreSQL để test API.

Chạy sau khi đã setup DB:
    python scripts/seed_demo_data.py
"""
import json
import random
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("Cài psycopg2: pip install psycopg2-binary")
    sys.exit(1)

from config.settings import DB_URL

# ── Dữ liệu mẫu ──────────────────────────────────────────────────────────────
PRODUCTS = [
    ("ABC001", "Điện thoại Samsung Galaxy A55", "Điện tử"),
    ("ABC002", "Laptop Asus VivoBook 15",       "Máy tính"),
    ("ABC003", "Tai nghe Sony WH-1000XM5",      "Phụ kiện"),
]

POSITIVE_REVIEWS = [
    ("Sản phẩm tuyệt vời", "Chất lượng rất tốt, đúng như mô tả. Giao hàng nhanh, đóng gói cẩn thận."),
    ("Rất hài lòng", "Mua lần 2 rồi, chất lượng ổn định. Giá cả hợp lý, sẽ tiếp tục ủng hộ shop."),
    ("Giao hàng siêu nhanh", "Đặt hôm trước hôm sau nhận. Sản phẩm đúng mẫu, không có lỗi gì."),
    ("Đáng đồng tiền", "So với giá tiền thì chất lượng rất ổn. Camera chụp đẹp, pin trâu."),
    ("Xuất sắc, 5 sao", "Hoàn toàn hài lòng. Màn hình sắc nét, máy chạy mượt mà."),
    ("Tốt hơn mong đợi", "Thật sự ấn tượng. Chất lượng vượt trội so với phân khúc giá."),
    ("Shop uy tín", "Sản phẩm chính hãng, có tem bảo hành. Shop tư vấn nhiệt tình."),
]

NEGATIVE_REVIEWS = [
    ("Thất vọng", "Sản phẩm không như quảng cáo. Chất lượng kém hơn nhiều so với ảnh."),
    ("Hàng lỗi", "Nhận được hàng bị lỗi, liên hệ shop phản hồi chậm. Rất bực."),
    ("Không đáng tiền", "Giá cao nhưng chất lượng thấp. Sẽ không mua lại."),
    ("Pin yếu", "Pin tụt nhanh, không được như quảng cáo. Chỉ dùng được 3-4 tiếng."),
    ("Giao hàng chậm", "Đặt hàng 1 tuần mới nhận. Shop không cập nhật trạng thái đơn."),
]

NEUTRAL_REVIEWS = [
    ("Tạm ổn", "Sản phẩm dùng được, không có gì đặc sắc. Bình thường so với phân khúc giá."),
    ("Được, nhưng có vài điểm cần cải thiện", "Nhìn chung ổn. Tuy nhiên pin chưa trâu, máy hơi nóng khi dùng lâu."),
    ("Mua về dùng thử", "Chất lượng tạm chấp nhận. Chưa dùng đủ lâu để đánh giá kỹ."),
]

TOPIC_MAP = {
    "positive": [["giao_hàng", "chất_lượng"], ["pin", "camera"], ["giá_cả", "đóng_gói"], ["màn_hình", "hiệu_năng"]],
    "negative": [["chất_lượng"], ["pin", "giao_hàng"], ["giá_cả"], ["lỗi"]],
    "neutral":  [["hiệu_năng"], ["pin"], ["giá_cả", "chất_lượng"]],
}

TOKEN_MAP = {
    "positive": [["sản_phẩm","tốt","giao_hàng","nhanh","đóng_gói","cẩn_thận"],
                 ["chất_lượng","ổn_định","pin","trâu","màn_hình","đẹp"],
                 ["giá_cả","hợp_lý","chính_hãng","uy_tín"]],
    "negative": [["sản_phẩm","kém","không_như_quảng_cáo","thất_vọng"],
                 ["lỗi","không_dùng","shop","phản_hồi","chậm"],
                 ["pin","yếu","nóng","chậm"]],
    "neutral":  [["sản_phẩm","tạm_ổn","bình_thường"],
                 ["pin","chưa_trâu","dùng_lâu","nóng"]],
}


def generate_reviews(n: int = 100) -> list[dict]:
    """Tạo n đánh giá mẫu ngẫu nhiên."""
    reviews = []
    base_date = datetime(2026, 1, 1)

    for i in range(n):
        product_id = random.choice([p[0] for p in PRODUCTS])

        # Phân phối sentiment: 65% pos, 20% neg, 15% neutral
        rand = random.random()
        if rand < 0.65:
            sentiment = "positive"
            rating = random.choice([4, 4, 5, 5, 5])
            score = round(random.uniform(0.65, 0.98), 2)
            title, content = random.choice(POSITIVE_REVIEWS)
        elif rand < 0.85:
            sentiment = "negative"
            rating = random.choice([1, 1, 2, 2, 3])
            score = round(random.uniform(0.05, 0.35), 2)
            title, content = random.choice(NEGATIVE_REVIEWS)
        else:
            sentiment = "neutral"
            rating = random.choice([3, 3, 4])
            score = round(random.uniform(0.4, 0.6), 2)
            title, content = random.choice(NEUTRAL_REVIEWS)

        created_at = base_date + timedelta(
            days=random.randint(0, 130),
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59),
        )

        reviews.append({
            "review_id":       f"DEMO_{i+1:05d}",
            "product_id":      product_id,
            "user_id":         f"USER_{random.randint(1000, 9999)}",
            "rating":          rating,
            "title":           title,
            "content":         content,
            "content_raw":     content,
            "created_at":      created_at,
            "helpful_count":   random.randint(0, 50),
            "images":          [],
            "sentiment":       sentiment,
            "sentiment_score": score,
            "tokens":          random.choice(TOKEN_MAP[sentiment]),
            "topics":          random.choice(TOPIC_MAP[sentiment]),
        })

    return reviews


def seed(n: int = 100):
    """Insert dữ liệu mẫu vào PostgreSQL."""
    print(f"🌱 Tạo {n} đánh giá mẫu...")

    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        # Insert products
        for pid, name, cat in PRODUCTS:
            cur.execute("""
                INSERT INTO products (product_id, name, category)
                VALUES (%s, %s, %s) ON CONFLICT (product_id) DO NOTHING
            """, (pid, name, cat))

        # Insert reviews
        reviews = generate_reviews(n)
        inserted = 0
        for r in reviews:
            try:
                cur.execute("""
                    INSERT INTO reviews (
                        review_id, product_id, user_id, rating, title, content, content_raw,
                        created_at, helpful_count, images, sentiment, sentiment_score, tokens, topics
                    ) VALUES (
                        %(review_id)s, %(product_id)s, %(user_id)s, %(rating)s,
                        %(title)s, %(content)s, %(content_raw)s, %(created_at)s,
                        %(helpful_count)s, %(images)s, %(sentiment)s, %(sentiment_score)s,
                        %(tokens)s, %(topics)s
                    ) ON CONFLICT (review_id) DO NOTHING
                """, r)
                inserted += 1
            except Exception as e:
                print(f"  ⚠ Lỗi insert {r['review_id']}: {e}")

        conn.commit()
        cur.close()
        conn.close()

        print(f"✅ Đã insert {inserted}/{n} đánh giá mẫu vào database.")
        print(f"   Products: {[p[0] for p in PRODUCTS]}")

    except psycopg2.OperationalError as e:
        print(f"❌ Không kết nối được DB: {e}")
        print("   Kiểm tra lại cấu hình DB trong .env")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Tạo dữ liệu mẫu cho Tiki Review System")
    parser.add_argument("--count", type=int, default=100, help="Số đánh giá cần tạo (default: 100)")
    args = parser.parse_args()
    seed(args.count)
