"""
config/settings.py
Cấu hình trung tâm cho toàn bộ hệ thống thu thập đánh giá Tiki.
Đọc từ biến môi trường (.env) kết hợp với giá trị mặc định.
"""
import os
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return False

load_dotenv()

# ─── Thông tin Database ────────────────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", 5432))
DB_NAME     = os.getenv("DB_NAME", "tiki_reviews")
DB_USER     = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")
DB_URL      = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# ─── RabbitMQ ─────────────────────────────────────────────────────────────────
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", 5672))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")
RABBITMQ_QUEUE = "tiki_reviews_raw"

# ─── Redis (Cache) ────────────────────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ─── Crawler Config ───────────────────────────────────────────────────────────
# Delay giữa mỗi request (giây) — tuân thủ robots.txt
CRAWL_DELAY        = float(os.getenv("CRAWL_DELAY", 1.0))
# Số request đồng thời tối đa
MAX_CONCURRENT     = int(os.getenv("MAX_CONCURRENT", 5))
# Số lần retry khi request thất bại
MAX_RETRIES        = int(os.getenv("MAX_RETRIES", 3))
# Timeout (giây)
REQUEST_TIMEOUT    = int(os.getenv("REQUEST_TIMEOUT", 30))
# Số đánh giá mỗi trang
REVIEWS_PER_PAGE   = 10

# User-Agent để tránh bị chặn — khai báo rõ ràng là bot nghiên cứu
USER_AGENT = (
    "Mozilla/5.0 (compatible; TikiReviewResearchBot/1.0; "
    "+https://github.com/your-repo/tiki-reviews)"
)

# Headers mặc định
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/html;q=0.9",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Referer": "https://tiki.vn",
}

# ─── API Config ───────────────────────────────────────────────────────────────
API_HOST       = os.getenv("API_HOST", "0.0.0.0")
API_PORT       = int(os.getenv("API_PORT", 8000))
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "change-me-in-production")
# Số đánh giá tối đa trả về mỗi request
API_MAX_LIMIT  = 100

# ─── NLP Config ───────────────────────────────────────────────────────────────
# Đường dẫn file stopwords tiếng Việt
STOPWORDS_PATH = os.getenv("STOPWORDS_PATH", "config/vietnamese_stopwords.txt")
# Số chủ đề cho LDA
LDA_NUM_TOPICS = int(os.getenv("LDA_NUM_TOPICS", 10))
# Ngưỡng điểm cảm xúc
SENTIMENT_THRESHOLD = 0.5

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE  = os.getenv("LOG_FILE", "logs/app.log")
