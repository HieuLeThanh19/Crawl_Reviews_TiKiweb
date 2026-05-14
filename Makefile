# Makefile — Lệnh tắt cho hệ thống Tiki Review
# Sử dụng: make <target>

.PHONY: help setup install db-setup seed api worker crawl test lint clean docker-up docker-down dashboard

# ── Hiển thị help ─────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════╗"
	@echo "║       Tiki Review System — Makefile Commands             ║"
	@echo "╠══════════════════════════════════════════════════════════╣"
	@echo "║  make setup        Cài đặt môi trường ban đầu           ║"
	@echo "║  make install      Cài Python dependencies               ║"
	@echo "║  make db-setup     Tạo schema PostgreSQL                 ║"
	@echo "║  make seed         Tạo 100 đánh giá mẫu vào DB          ║"
	@echo "║  make api          Khởi động FastAPI server              ║"
	@echo "║  make worker       Khởi động ETL worker                  ║"
	@echo "║  make crawl        Crawl sản phẩm mẫu ABC001             ║"
	@echo "║  make test         Chạy toàn bộ unit tests               ║"
	@echo "║  make lint         Kiểm tra code style                   ║"
	@echo "║  make dashboard    Mở dashboard trong trình duyệt        ║"
	@echo "║  make docker-up    Khởi động tất cả services (Docker)    ║"
	@echo "║  make docker-down  Dừng tất cả services                  ║"
	@echo "║  make clean        Xóa file tạm                          ║"
	@echo "╚══════════════════════════════════════════════════════════╝"
	@echo ""

# ── Setup môi trường ──────────────────────────────────────────────────────────
setup:
	@echo "🔧 Thiết lập môi trường..."
	python -m venv venv
	@echo "✅ Đã tạo virtualenv. Kích hoạt: source venv/bin/activate"
	mkdir -p logs models backups
	@[ -f .env ] || cp .env.example .env && echo "✅ .env đã tạo từ .env.example"

install:
	@echo "📦 Cài đặt dependencies..."
	pip install -r requirements.txt
	@echo "✅ Xong!"

# ── Database ──────────────────────────────────────────────────────────────────
db-setup:
	@echo "🗄️  Tạo schema PostgreSQL..."
	psql -U postgres -f scripts/setup_db.sql
	@echo "✅ Schema đã được tạo!"

seed:
	@echo "🌱 Tạo dữ liệu mẫu..."
	python scripts/seed_demo_data.py --count 100

# ── Services ──────────────────────────────────────────────────────────────────
api:
	@echo "🚀 Khởi động FastAPI server tại http://localhost:8000"
	python main.py api --reload

worker:
	@echo "⚙️  Khởi động ETL worker..."
	python main.py worker

crawl:
	@echo "🔍 Crawl sản phẩm ABC001..."
	python main.py crawl --product-id ABC001 --max-pages 10 --no-queue

dashboard:
	@echo "🌐 Mở Dashboard..."
	python main.py dashboard

# ── Testing ───────────────────────────────────────────────────────────────────
test:
	@echo "🧪 Chạy unit tests..."
	pytest tests/ -v --tb=short

test-etl:
	pytest tests/test_etl.py -v

test-crawler:
	pytest tests/test_crawler.py -v

test-api:
	pytest tests/test_api.py -v

lint:
	@echo "🔍 Kiểm tra code style..."
	@flake8 . --max-line-length=120 --exclude=venv,__pycache__ 2>/dev/null || echo "(flake8 chưa cài: pip install flake8)"

# ── Docker ────────────────────────────────────────────────────────────────────
docker-up:
	@echo "🐳 Khởi động Docker services..."
	docker compose up -d
	@echo "✅ Các services đang chạy:"
	@echo "   API:      http://localhost:8000"
	@echo "   Swagger:  http://localhost:8000/api/docs"
	@echo "   RabbitMQ: http://localhost:15672 (guest/guest)"

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f --tail=100

docker-build:
	docker compose build --no-cache

# ── Backup ────────────────────────────────────────────────────────────────────
backup:
	python scripts/backup_db.py backup

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@echo "✅ Đã xóa file tạm."
