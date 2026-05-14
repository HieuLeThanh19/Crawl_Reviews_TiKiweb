FROM python:3.11-slim

# Cài đặt dependencies hệ thống
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements trước (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download mô hình Underthesea (nếu cần)
RUN python -c "import underthesea; underthesea.download('SENT')" || true

# Copy source code
COPY . .

# Tạo thư mục cần thiết
RUN mkdir -p logs models

# Non-root user cho bảo mật
RUN useradd -m -r appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Default command: chạy API
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
