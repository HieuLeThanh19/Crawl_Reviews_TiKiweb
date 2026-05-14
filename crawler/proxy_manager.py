"""
crawler/proxy_manager.py
Quản lý danh sách proxy để tránh bị block khi crawl.
Hỗ trợ rotation tự động và đánh dấu proxy hỏng.
"""
import asyncio
import logging
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Proxy:
    """Đại diện cho một proxy server."""
    url: str                    # Ví dụ: http://user:pass@host:port
    fail_count: int = 0
    is_active: bool = True
    response_time: float = 0.0  # giây

    def mark_failed(self):
        self.fail_count += 1
        if self.fail_count >= 3:
            self.is_active = False
            logger.warning(f"Proxy {self.url} bị vô hiệu hóa sau {self.fail_count} lỗi.")

    def reset(self):
        self.fail_count = 0
        self.is_active = True


class ProxyManager:
    """
    Quản lý pool proxy với rotation tự động.
    
    Sử dụng:
        pm = ProxyManager(["http://proxy1:port", "http://proxy2:port"])
        proxy = pm.get_proxy()
        pm.report_failure(proxy)
    """

    def __init__(self, proxy_urls: list[str] = None):
        self._proxies: deque[Proxy] = deque()
        self._lock = asyncio.Lock()

        if proxy_urls:
            for url in proxy_urls:
                self._proxies.append(Proxy(url=url))
            logger.info(f"Đã nạp {len(proxy_urls)} proxies vào pool.")
        else:
            logger.info("Chạy không có proxy — dùng IP thật.")

    def add_proxy(self, url: str):
        """Thêm proxy vào pool."""
        self._proxies.append(Proxy(url=url))

    def get_proxy(self) -> Optional[str]:
        """
        Lấy proxy tiếp theo (round-robin).
        Bỏ qua các proxy bị vô hiệu hóa.
        Trả về None nếu không có proxy khả dụng.
        """
        active = [p for p in self._proxies if p.is_active]
        if not active:
            return None
        # Random để tránh pattern dễ nhận diện
        proxy = random.choice(active)
        return proxy.url

    def report_failure(self, proxy_url: str):
        """Báo cáo proxy bị lỗi."""
        for proxy in self._proxies:
            if proxy.url == proxy_url:
                proxy.mark_failed()
                return

    def report_success(self, proxy_url: str, response_time: float = 0.0):
        """Cập nhật thành công cho proxy."""
        for proxy in self._proxies:
            if proxy.url == proxy_url:
                proxy.fail_count = max(0, proxy.fail_count - 1)
                proxy.response_time = response_time
                return

    @property
    def active_count(self) -> int:
        return sum(1 for p in self._proxies if p.is_active)

    @property
    def total_count(self) -> int:
        return len(self._proxies)

    def status(self) -> dict:
        return {
            "total": self.total_count,
            "active": self.active_count,
            "inactive": self.total_count - self.active_count,
        }
