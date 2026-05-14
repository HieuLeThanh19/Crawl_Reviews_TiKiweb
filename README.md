# Tiki Review System

Dashboard crawl review thật từ Tiki, nhập tên sản phẩm/link/product_id rồi xem đánh giá đã phân loại.

## Thư Mục Chính

```text
tiki_review_system/
├── api/
│   └── app.py                  # FastAPI server, mở dashboard và API
├── crawler/
│   ├── tiki_client.py          # Gọi Tiki search API và review API thật
│   ├── tiki_crawler.py         # Crawler async dùng cho batch/CLI
│   ├── scrapy_spider.py        # Spider Scrapy
│   └── proxy_manager.py        # Quản lý proxy
├── storage/
│   └── sqlite_store.py         # Lưu sản phẩm/review vào SQLite
├── ui/
│   └── dashboard.html          # Giao diện web
├── data/
│   └── tiki_reviews.sqlite3    # Database local
├── logs/
├── models/
├── requirements.txt            # Thư viện cần cài
└── README.md
```

## Chạy Web Dashboard

Mở PowerShell trong thư mục project:

```powershell
cd C:\Users\letha\Downloads\tiki_review_system\tiki_review_system
```

Cài thư viện nếu máy chưa cài:

```powershell
python -m pip install -r requirements.txt
```

Chạy server:

```powershell
python -m uvicorn api.app:app --host 127.0.0.1 --port 8010
```

Mở link web:

```text
http://127.0.0.1:8010
```

## Nếu Port Bị Chiếm

Đổi sang port khác, ví dụ `8020`:

```powershell
python -m uvicorn api.app:app --host 127.0.0.1 --port 8020
```

Mở:

```text
http://127.0.0.1:8020
```

## Cách Dùng

1. Nhập tên sản phẩm, link Tiki hoặc `product_id`.
2. Bấm `Tìm sản phẩm`.
3. Chọn đúng sản phẩm để thấy `product_id` và `spid`.
4. Bấm `Crawl review`.
5. Xem bảng đánh giá, lọc theo sao hoặc phân loại `Tích cực / Trung lập / Tiêu cực`.
