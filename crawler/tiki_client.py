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
DEFAULT_CATEGORY = "1789"


class TikiClientError(RuntimeError):
    """Raised when Tiki returns an unexpected response."""


class TikiClient:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def search_products(
        self,
        query: str,
        limit: int = 10,
        category: str = DEFAULT_CATEGORY,
    ) -> list[dict[str, Any]]:
        """Search public Tiki listings and return compact product records."""
        query = query.strip()
        if not query:
            return []

        params = {
            "q": query,
            "limit": max(1, min(limit, 40)),
            "page": 1,
        }
        if category:
            params["category"] = category
        response = self.session.get(TIKI_SEARCH_API, params=params, timeout=self.timeout)
        response.raise_for_status()
        response.encoding = "utf-8"
        payload = response.json()

        products = []
        for item in payload.get("data", []):
            product_id = str(item.get("id") or "")
            if not product_id:
                continue

            url_path = item.get("url_path") or ""
            products.append(
                {
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
            )
        return products

    def fetch_reviews(self, product_id: str, max_pages: int = 3) -> dict[str, Any]:
        """Fetch public reviews for one product from Tiki."""
        product_id = str(product_id).strip()
        if not product_id:
            raise TikiClientError("product_id is required")
        max_pages = max(1, int(max_pages or 1))

        reviews: list[dict[str, Any]] = []
        summary: dict[str, Any] = {
            "product_id": product_id,
            "reviews_count": 0,
            "rating_average": 0,
            "stars": {},
            "last_page": 0,
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

            paging = data.get("paging") or {}
            if page >= int(paging.get("last_page") or 1):
                break

            time.sleep(0.4)

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
