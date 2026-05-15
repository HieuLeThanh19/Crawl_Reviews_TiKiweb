"""
Small synchronous client for public Tiki product search and review endpoints.

The existing aiohttp crawler is still useful for batch jobs.  This module is
kept deliberately simple so the API and UI can run in a student project without
RabbitMQ or PostgreSQL: search products, fetch public reviews, and normalize
them into the same review shape used by the rest of the app.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from config.settings import DEFAULT_HEADERS, REVIEWS_PER_PAGE


TIKI_SEARCH_API = "https://tiki.vn/api/personalish/v1/blocks/listings"
TIKI_REVIEW_API = "https://tiki.vn/api/v2/reviews"
DEFAULT_CATEGORY = ""
SEARCH_CATEGORY_GROUPS = {
    "": (
        "1789",  # Dien thoai - May tinh bang
        "1846",  # Laptop - May vi tinh
        "1882",  # Thiet bi so - Phu kien so
        "1815",  # Phu kien
        "8322",  # Nha sach
        "1520",  # Lam dep - Suc khoe
        "1883",  # Nha cua - Doi song
        "1975",  # Dien gia dung
        "8594",  # Me va be
        "915",  # Thoi trang nu
        "1686",  # Thoi trang nam
        "1703",  # The thao - Da ngoai
        "4384",  # Bach hoa online
    ),
    "phones": ("1789", "1882", "1815"),
    "computers": ("1846", "1882", "1815"),
    "digital": ("1882", "1815", "1789"),
    "books": ("8322",),
    "beauty": ("1520",),
    "home": ("1883", "1975"),
    "appliances": ("1975", "1883"),
    "baby": ("8594", "4384"),
    "fashion": ("915", "1686"),
    "sports": ("1703",),
    "grocery": ("4384", "8594"),
}


class TikiClientError(RuntimeError):
    """Raised when Tiki returns an unexpected response."""


class TikiClient:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(DEFAULT_HEADERS)

    def search_products(
        self,
        query: str,
        limit: int = 10,
        category: str = DEFAULT_CATEGORY,
        pages: int = 1,
    ) -> list[dict[str, Any]]:
        """Search public Tiki listings and return compact product records."""
        query = query.strip()
        if not query:
            return []

        per_page = max(1, min(limit, 40))
        max_pages = max(1, min(int(pages or 1), 25))
        products_by_id: dict[str, dict[str, Any]] = {}
        categories = list(SEARCH_CATEGORY_GROUPS.get(category, (category,)))
        pages_per_category = max_pages
        if category in ("", "phones", "computers", "digital", "home", "baby", "fashion", "grocery"):
            pages_per_category = max(1, min(max_pages, 3))

        for search_category in categories:
            for page in range(1, pages_per_category + 1):
                params = {
                    "q": query,
                    "limit": per_page,
                    "page": page,
                    "category": search_category,
                }
                response = self.session.get(TIKI_SEARCH_API, params=params, timeout=self.timeout)
                response.raise_for_status()
                response.encoding = "utf-8"
                payload = response.json()

                raw_items = payload.get("data", [])
                if not raw_items:
                    break

                for item in raw_items:
                    product_id = str(item.get("id") or "")
                    if not product_id:
                        continue

                    url_path = item.get("url_path") or ""
                    products_by_id[product_id] = {
                        "product_id": product_id,
                        "spid": str(item.get("seller_product_id") or item.get("spid") or ""),
                        "name": item.get("name") or "",
                        "brand": item.get("brand_name") or "",
                        "price": item.get("price") or 0,
                        "rating_average": item.get("rating_average") or 0,
                        "review_count": item.get("review_count") or 0,
                        "quantity_sold": (item.get("quantity_sold") or {}).get("text", ""),
                        "thumbnail_url": item.get("thumbnail_url") or "",
                        "url": f"https://tiki.vn/{url_path}" if url_path else "",
                    }

                paging = payload.get("paging") or {}
                last_page = int(paging.get("last_page") or pages_per_category)
                if page >= last_page:
                    break

                time.sleep(0.15)

            if len(categories) > 1:
                time.sleep(0.1)

        return sorted(
            products_by_id.values(),
            key=lambda item: (int(item.get("review_count") or 0), float(item.get("rating_average") or 0)),
            reverse=True,
        )

    def fetch_reviews(
        self,
        product_id: str,
        max_pages: int = 3,
        max_reviews: int | None = None,
    ) -> dict[str, Any]:
        """Fetch public reviews for one product from Tiki."""
        product_id = str(product_id).strip()
        if not product_id:
            raise TikiClientError("product_id is required")
        max_pages = max(1, int(max_pages or 1))
        max_reviews = int(max_reviews or 0) or None

        reviews: list[dict[str, Any]] = []
        summary: dict[str, Any] = {
            "product_id": product_id,
            "reviews_count": 0,
            "listing_reviews_count": max_reviews or 0,
            "rating_average": 0,
            "stars": {},
            "last_page": 0,
            "requested_pages": max_pages,
            "fetched_pages": 0,
            "per_page": REVIEWS_PER_PAGE,
            "crawl_limit_note": "",
        }

        for page in range(1, max_pages + 1):
            params = {
                "product_id": product_id,
                "limit": REVIEWS_PER_PAGE,
                "page": page,
                "sort": "score|desc,id|desc,stars|all",
            }
            response = self.session.get(TIKI_REVIEW_API, params=params, timeout=self.timeout)
            response.raise_for_status()
            response.encoding = "utf-8"
            data = response.json()

            if page == 1:
                paging = data.get("paging") or {}
                summary.update(
                    {
                        "reviews_count": data.get("reviews_count") or paging.get("total") or 0,
                        "rating_average": data.get("rating_average") or 0,
                        "stars": data.get("stars") or {},
                        "last_page": paging.get("last_page") or 0,
                    }
                )

            raw_reviews = data.get("data") or []
            if not raw_reviews:
                break

            reviews.extend(_parse_review(raw, product_id) for raw in raw_reviews)
            if max_reviews is not None and len(reviews) >= max_reviews:
                reviews = reviews[:max_reviews]
                summary["fetched_pages"] = page
                break
            summary["fetched_pages"] = page

            paging = data.get("paging") or {}
            if page >= int(paging.get("last_page") or 1):
                break

            time.sleep(0.4)

        if max_reviews is not None and summary["reviews_count"] > max_reviews:
            summary["crawl_limit_note"] = (
                "The public review API returned a larger master-product total; "
                "results were capped to the selected listing review count."
            )
        elif summary["reviews_count"] and len(reviews) < int(summary["reviews_count"]):
            summary["crawl_limit_note"] = (
                "Tiki reports more total reviews than the public API pages currently returned."
            )

        return {"summary": summary, "reviews": reviews}


def _parse_review(raw: dict[str, Any], product_id: str) -> dict[str, Any]:
    created_by = raw.get("created_by", {}) or {}
    return {
        "review_id": str(raw.get("id", "")),
        "product_id": product_id,
        "user_id": str(created_by.get("id", "")),
        "rating": int(raw.get("rating", 0)),
        "title": raw.get("title", ""),
        "content": raw.get("content", ""),
        "created_at": raw.get("created_at", ""),
        "helpful_count": int(raw.get("thank_count", 0)),
        "images": [img.get("full_path", "") for img in (raw.get("images") or [])],
        "sentiment": None,
        "tokens": [],
        "topics": [],
        "_crawled_at": time.time(),
        "_raw_status": raw.get("status", ""),
    }
