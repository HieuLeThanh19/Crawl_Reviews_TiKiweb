#!/usr/bin/env python3
"""
main.py — Entry point cho hệ thống thu thập đánh giá Tiki

"""
import argparse
import logging
import os
import subprocess
import sys
import webbrowser

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/app.log") if os.path.exists("logs") else logging.NullHandler(),
    ]
)
logger = logging.getLogger("main")


def cmd_crawl(args):
    """Crawl đánh giá sản phẩm."""
    from crawler.tiki_client import TikiClient
    from storage import sqlite_store
    import json

    result = TikiClient().fetch_reviews(args.product_id, args.max_pages)
    reviews = result["reviews"]
    output = args.output or f"reviews_{args.product_id}.json"
    with open(output, "w", encoding="utf-8") as f:
        json.dump(reviews, f, ensure_ascii=False, indent=2)

    sqlite_store.save_product({
        "product_id": args.product_id,
        "spid": "",
        "name": f"Tiki product {args.product_id}",
        "brand": "",
        "price": 0,
        "rating_average": result["summary"].get("rating_average") or 0,
        "review_count": result["summary"].get("reviews_count") or 0,
        "quantity_sold": "",
        "thumbnail_url": "",
        "url": "",
    })
    inserted = sqlite_store.save_reviews(args.product_id, reviews)

    print(f"\nHoàn thành! Lấy {len(reviews)} review thật, lưu mới {inserted} review.")
    print(f"File JSON: {output}")
    print(f"SQLite: {sqlite_store.DB_PATH}")


def cmd_search(args):
    """Tìm sản phẩm thật trên Tiki và in product_id."""
    from crawler.tiki_client import TikiClient
    from storage import sqlite_store

    products = TikiClient().search_products(args.query, limit=args.limit, category=args.category)
    sqlite_store.save_products(products)
    if not products:
        print("Không tìm thấy sản phẩm.")
        return

    for idx, product in enumerate(products, start=1):
        print(f"{idx}. {product['name']}")
        print(f"   product_id: {product['product_id']} | spid: {product.get('spid') or 'N/A'}")
        print(f"   rating: {product.get('rating_average', 0)}/5 | reviews: {product.get('review_count', 0)} | price: {product.get('price', 0)}")
        print(f"   url: {product.get('url') or 'N/A'}")


def cmd_worker(args):
    """Chạy ETL worker lắng nghe queue."""
    from etl.pipeline import ETLPipeline
    pipeline = ETLPipeline()
    pipeline.run_worker()


def cmd_etl_file(args):
    """Xử lý file JSON và lưu vào DB."""
    from etl.pipeline import ETLPipeline
    pipeline = ETLPipeline()
    stats = pipeline.process_file(args.file)
    print(f"\n✅ ETL hoàn thành: {stats}")


def cmd_api(args):
    """Khởi động FastAPI server."""
    import uvicorn
    uvicorn.run(
        "api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1 if args.reload else args.workers,
    )


def cmd_train_lda(args):
    """Train LDA topic model từ dữ liệu trong DB."""
    from etl.pipeline import ETLPipeline
    pipeline = ETLPipeline()
    pipeline.train_topic_model(args.save_path)


def cmd_dashboard(args):
    """Mở dashboard trực tiếp trong trình duyệt."""
    html_path = os.path.abspath("ui/dashboard.html")
    if os.path.exists(html_path):
        url = f"file://{html_path}"
        print(f"🌐 Mở dashboard: {url}")
        webbrowser.open(url)
    else:
        print("❌ Không tìm thấy ui/dashboard.html")


def cmd_setup(args):
    """Thiết lập database và môi trường."""
    print("🔧 Thiết lập hệ thống...")
    # Tạo thư mục cần thiết
    os.makedirs("logs", exist_ok=True)
    os.makedirs("models", exist_ok=True)
    print("✅ Đã tạo thư mục logs/ và models/")

    # Copy .env.example nếu chưa có .env
    if not os.path.exists(".env") and os.path.exists(".env.example"):
        import shutil
        shutil.copy(".env.example", ".env")
        print("✅ Đã tạo .env từ .env.example — vui lòng chỉnh sửa trước khi chạy")

    print("\n📋 Bước tiếp theo:")
    print("  1. Chỉnh sửa .env (DB password, API key...)")
    print("  2. psql -U postgres -f scripts/setup_db.sql")
    print("  3. docker-compose up -d  (hoặc chạy từng service)")
    print("  4. python main.py api")
    print("  5. python main.py dashboard  (xem giao diện demo)")


# ── Parser ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Hệ thống thu thập & phân tích đánh giá Tiki",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="cmd", required=True)

    # crawl
    p_crawl = subs.add_parser("crawl", help="Thu thập đánh giá sản phẩm")
    p_crawl.add_argument("--product-id", required=True)
    p_crawl.add_argument("--max-pages", type=int, default=100)
    p_crawl.add_argument("--output", default=None)
    p_crawl.add_argument("--no-queue", action="store_true")
    p_crawl.set_defaults(func=cmd_crawl)

    # search
    p_search = subs.add_parser("search", help="Tìm sản phẩm thật trên Tiki và hiện product_id")
    p_search.add_argument("query", help="Từ khóa sản phẩm, ví dụ: iphone")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--category", default="1789", help="Category Tiki, mặc định 1789")
    p_search.set_defaults(func=cmd_search)

    # worker
    p_worker = subs.add_parser("worker", help="ETL worker (lắng nghe RabbitMQ)")
    p_worker.set_defaults(func=cmd_worker)

    # etl-file
    p_etl = subs.add_parser("etl-file", help="Xử lý ETL từ file JSON")
    p_etl.add_argument("file", help="Đường dẫn file JSON reviews")
    p_etl.set_defaults(func=cmd_etl_file)

    # api
    p_api = subs.add_parser("api", help="Khởi động FastAPI server")
    p_api.add_argument("--host", default="0.0.0.0")
    p_api.add_argument("--port", type=int, default=8000)
    p_api.add_argument("--reload", action="store_true")
    p_api.add_argument("--workers", type=int, default=4)
    p_api.set_defaults(func=cmd_api)

    # train-lda
    p_lda = subs.add_parser("train-lda", help="Train LDA topic model")
    p_lda.add_argument("--save-path", default="models/lda_model")
    p_lda.set_defaults(func=cmd_train_lda)

    # dashboard
    p_db = subs.add_parser("dashboard", help="Mở dashboard trong trình duyệt")
    p_db.set_defaults(func=cmd_dashboard)

    # setup
    p_setup = subs.add_parser("setup", help="Thiết lập môi trường ban đầu")
    p_setup.set_defaults(func=cmd_setup)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
