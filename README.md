# Tiki Review System

Dashboard tìm sản phẩm thật trên Tiki, chọn đúng `product_id`, crawl review công khai rồi xem dữ liệu đã lưu trong SQLite.

## Cấu Trúc Chính

```text
tiki_review_system/
├── api/
│   └── app.py                  # FastAPI server, dashboard và API
├── crawler/
│   ├── tiki_client.py          # Search sản phẩm và crawl review từ Tiki API công khai
│   ├── tiki_crawler.py         # Crawler async cho batch/CLI
│   ├── scrapy_spider.py        # Spider Scrapy
│   └── proxy_manager.py        # Quản lý proxy
├── storage/
│   └── sqlite_store.py         # Lưu sản phẩm/review vào SQLite
├── ui/
│   └── dashboard.html          # Giao diện web
├── data/
│   └── tiki_reviews.sqlite3    # Database local
├── requirements.txt
└── README.md
```

## Chạy Dashboard

Mở PowerShell trong thư mục project:

```powershell
cd C:\Users\letha\OneDrive\Desktop\TiKi\tiki_review_system
```

Cài thư viện nếu máy chưa có:

```powershell
python -m pip install -r requirements.txt
```

Chạy server:

```powershell
python -m uvicorn api.app:app --host 127.0.0.1 --port 8010
```

Mở:

```text
http://127.0.0.1:8010
```

Nếu port `8010` đang bận, đổi sang port khác, ví dụ:

```powershell
python -m uvicorn api.app:app --host 127.0.0.1 --port 8011
```

Mở:

```text
http://127.0.0.1:8011
```

## Cách Demo

1. Nhập tên sản phẩm, link Tiki hoặc `product_id`.
2. Chọn phạm vi tìm kiếm: `Tất cả ngành hàng`, `Điện thoại - Phụ kiện`, `Mẹ và bé`, `Thời trang`, `Bách hóa online`, ...
3. Chọn số trang tìm kiếm. Kết quả được gom từ các ngành liên quan, bỏ trùng và xếp theo số review từ cao xuống thấp.
4. Bấm `Tìm sản phẩm`.
5. Chọn đúng sản phẩm trong danh sách. Bảng review sẽ chưa tự hiện dữ liệu cũ.
6. Chọn số trang crawl rồi bấm `Crawl review`.
7. Sau khi crawl xong, dùng `Crawl lại`, lọc sao/phân loại hoặc `Xuất CSV`.

## Hiểu Đúng Số Review

- `Review Tiki báo` là tổng số review Tiki thống kê cho sản phẩm.
- `Review local hiện tại` là số review app đang lưu cho lần crawl mới nhất của sản phẩm đó.
- Khi bấm `Crawl review` hoặc `Crawl lại`, app sẽ làm mới bộ review local của sản phẩm đang chọn. Ví dụ trước đó crawl 50 trang được 971 review, sau đó chọn `Crawl 1 trang` thì dashboard sẽ đổi về bộ 1 trang mới, khoảng 20 review nếu Tiki trả đủ.
- Một số endpoint review public của Tiki có thể trả tổng review cấp sản phẩm gộp/master lớn hơn số review trên link hoặc listing đang chọn. App sẽ chặn số review local tối đa theo `review_count` của sản phẩm đã chọn để tránh lưu vượt số review hiển thị trên link gốc.
- Bảng review có phân trang, mặc định hiện 20 dòng/trang. Có thể đổi sang `Hiện 50 dòng` hoặc `Hiện 100 dòng`.
- Nếu Tiki báo vài trăm hoặc vài nghìn review nhưng local chỉ có một phần, đó thường là do endpoint review công khai chỉ trả một số trang public nhất định. App sẽ hiển thị phần đã crawl được, không tự giả lập thêm dữ liệu.

Ví dụ query demo ổn:

```text
iphone          -> Điện thoại - Phụ kiện hoặc Tất cả ngành hàng
laptop          -> Laptop - Máy vi tính
sữa             -> Mẹ và bé hoặc Bách hóa online
áo              -> Thời trang
kem chống nắng  -> Làm đẹp - Sức khỏe
gạo             -> Bách hóa online
```

## Lưu Ý An Toàn

- App chỉ crawl review công khai từ endpoint Tiki.
- Không tự crawl trước khi người dùng chọn sản phẩm và bấm `Crawl review`.
- Nên bắt đầu với `Crawl 3 trang` hoặc `Crawl 10 trang` khi demo.
- `Crawl 50 trang` và `Crawl 100 trang` có thể mất lâu hơn; chỉ dùng khi cần nhiều dữ liệu để test.
- Nếu SQLite báo `disk I/O error`, thường là do OneDrive hoặc tiến trình khác đang khóa file `data/tiki_reviews.sqlite3`. Hãy tắt server cũ hoặc chạy project ngoài thư mục OneDrive nếu cần crawl/lưu nhiều.

## API Nhanh

Tìm sản phẩm:

```text
GET /api/tiki/search?q=iphone&category=phones&limit=40&pages=3
```

Crawl review:

```text
POST /api/tiki/crawl
```

Payload:

```json
{
  "product_id": "197214029",
  "max_pages": 10,
  "replace_existing": true
}
```

Xem review đã lưu:

```text
GET /api/products/{product_id}/reviews
```
