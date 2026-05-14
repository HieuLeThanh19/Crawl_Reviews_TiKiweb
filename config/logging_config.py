"""
config/logging_config.py
Cấu hình logging tập trung cho toàn bộ hệ thống.

Sử dụng:
    from config.logging_config import setup_logging
    setup_logging()
"""
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from config.settings import LOG_LEVEL, LOG_FILE


def setup_logging(
    level: str = None,
    log_file: str = None,
    max_bytes: int = 10 * 1024 * 1024,   # 10MB
    backup_count: int = 5,
) -> logging.Logger:
    """
    Thiết lập logging với:
      - Console handler (stdout): INFO+
      - Rotating file handler: WARNING+
      - Format rõ ràng với timestamp, module, level

    Args:
        level:        Log level (DEBUG/INFO/WARNING/ERROR)
        log_file:     Đường dẫn file log
        max_bytes:    Kích thước tối đa mỗi file log trước khi rotate
        backup_count: Số file backup giữ lại

    Returns:
        Root logger đã cấu hình
    """
    level = level or LOG_LEVEL
    log_file = log_file or LOG_FILE

    # Tạo thư mục log nếu chưa có
    log_dir = Path(log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # Format
    fmt = "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=date_fmt)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Xóa handlers cũ để tránh duplicate
    root_logger.handlers.clear()

    # ── Console handler ───────────────────────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(formatter)
    root_logger.addHandler(console)

    # ── Rotating file handler ─────────────────────────────────────────────────
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.WARNING)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except (OSError, PermissionError) as e:
        root_logger.warning(f"Không thể tạo file log '{log_file}': {e}")

    # Giảm noise từ thư viện ngoài
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("scrapy").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    root_logger.info(f"Logging khởi tạo: level={level}, file={log_file}")
    return root_logger


# Chạy setup ngay khi import (optional)
if __name__ != "__main__":
    pass  # Gọi setup_logging() thủ công trong main.py
