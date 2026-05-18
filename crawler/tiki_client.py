"""
Small synchronous client for public Tiki product search and review endpoints.

The existing aiohttp crawler is still useful for batch jobs.  This module is
kept deliberately simple so the API and UI can run in a student project without
RabbitMQ or PostgreSQL: search products, fetch public reviews, and normalize
them into the same review shape used by the rest of the app.
"""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Any

import requests

from config.settings import DEFAULT_HEADERS, REVIEWS_PER_PAGE


TIKI_SEARCH_API = "https://tiki.vn/api/personalish/v1/blocks/listings"
TIKI_PRODUCT_SEARCH_API = "https://tiki.vn/api/v2/products"
TIKI_PRODUCT_API = "https://tiki.vn/api/v2/products/{product_id}"
TIKI_REVIEW_API = "https://tiki.vn/api/v2/reviews"
DEFAULT_CATEGORY = ""
REVIEW_CRAWL_BUCKETS = ("all", "1", "2", "3", "4", "5")
SEARCH_CATEGORY_GROUPS = {
    "": (
        "",
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

CATEGORY_REQUIRED_TERMS = {
    "phones": (
        "dien thoai", "smartphone", "iphone", "samsung", "xiaomi", "oppo",
        "vivo", "realme", "nokia", "tecno", "infinix", "pixel", "honor",
        "redmi", "poco", "may tinh bang", "tablet", "ipad", "op lung",
        "cuong luc", "sac", "cap", "tai nghe",
    ),
}

QUERY_STOPWORDS = {
    "va", "voi", "cho", "cua", "hang", "chinh", "hang chinh", "gia", "tot",
    "loai", "mau", "san", "pham", "tiki",
}

SEARCH_DETAIL_ENRICH_LIMIT = 35
SEARCH_RETURN_LIMIT = 160
SEARCH_PAGE_DELAY_SECONDS = 0.06
SEARCH_DETAIL_DELAY_SECONDS = 0.01
CRAWL_PAGE_DELAY_SECONDS = 0.08


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
        categories = _search_categories_for_query(query, category)
        pages_per_category = max_pages
        if category in ("", "phones", "computers", "digital", "home", "baby", "fashion", "grocery"):
            pages_per_category = max(1, min(max_pages, 5))

        for search_category in categories:
            for page in range(1, pages_per_category + 1):
                params = {
                    "q": query,
                    "limit": per_page,
                    "page": page,
                }
                if search_category:
                    params["category"] = search_category
                search_url = TIKI_PRODUCT_SEARCH_API if not search_category else TIKI_SEARCH_API
                response = self.session.get(search_url, params=params, timeout=self.timeout)
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

                    record = _product_record_from_payload(item)
                    existing = products_by_id.get(product_id)
                    if existing:
                        products_by_id[product_id] = _merge_product_records(existing, record)
                    else:
                        products_by_id[product_id] = record

                paging = payload.get("paging") or {}
                last_page = int(paging.get("last_page") or pages_per_category)
                if page >= last_page:
                    break

                time.sleep(SEARCH_PAGE_DELAY_SECONDS)

            if len(categories) > 1:
                time.sleep(SEARCH_PAGE_DELAY_SECONDS)

        ranked_products = [
            {**item, "_match_score": _product_match_score(query, item, category)}
            for item in products_by_id.values()
        ]
        relevant_products = [
            item for item in ranked_products
            if _is_relevant_product_match(query, item, category)
        ]
        if relevant_products:
            ranked_products = relevant_products

        ranked_products = self._enrich_search_results(query, ranked_products)
        ranked_products.sort(
            key=lambda item: (
                _demo_rank_bucket(query, item),
                -_accessory_mismatch_penalty(query, item),
                _primary_product_score(query, item),
                int(item.get("_match_score") or 0),
                _review_count_score(item),
                int(item.get("review_count") or 0),
                float(item.get("rating_average") or 0),
            ),
            reverse=True,
        )
        for item in ranked_products:
            item.pop("_match_score", None)
        return ranked_products[:SEARCH_RETURN_LIMIT]

    def _enrich_search_results(self, query: str, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        products.sort(
            key=lambda item: (
                _demo_rank_bucket(query, item),
                -_accessory_mismatch_penalty(query, item),
                _primary_product_score(query, item),
                int(item.get("_match_score") or 0),
                _review_count_score(item),
                int(item.get("review_count") or 0),
                float(item.get("rating_average") or 0),
            ),
            reverse=True,
        )
        enriched: list[dict[str, Any]] = []
        for index, item in enumerate(products):
            if index >= SEARCH_DETAIL_ENRICH_LIMIT:
                enriched.append(item)
                continue
            try:
                detail = self.get_product_detail(item["product_id"], spid=item.get("spid") or None)
                merged = _merge_product_records(item, detail)
                merged["_match_score"] = _product_match_score(query, merged)
                enriched.append(merged)
                time.sleep(SEARCH_DETAIL_DELAY_SECONDS)
            except (requests.RequestException, TikiClientError, ValueError):
                enriched.append(item)
        return enriched

    def get_product_detail(self, product_id: str, spid: str | None = None) -> dict[str, Any]:
        """Fetch current product/listing metadata from Tiki product detail API."""
        product_id = str(product_id).strip()
        if not product_id:
            raise TikiClientError("product_id is required")

        params = {"platform": "web"}
        spid = str(spid or "").strip()
        if spid:
            params["spid"] = spid

        response = self.session.get(
            TIKI_PRODUCT_API.format(product_id=product_id),
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        response.encoding = "utf-8"
        data = response.json()
        if not isinstance(data, dict) or not data.get("id"):
            raise TikiClientError("Tiki product detail response is invalid")
        return _product_record_from_payload(data)

    def fetch_reviews(
        self,
        product_id: str,
        max_pages: int = 3,
        max_reviews: int | None = None,
        spid: str | None = None,
    ) -> dict[str, Any]:
        """Fetch public reviews for one product from Tiki."""
        product_id = str(product_id).strip()
        if not product_id:
            raise TikiClientError("product_id is required")
        spid = str(spid or "").strip()
        max_pages = max(1, int(max_pages or 1))
        max_reviews = int(max_reviews or 0) or None

        reviews: list[dict[str, Any]] = []
        seen_review_ids: set[str] = set()
        requests_made = 0
        summary: dict[str, Any] = {
            "product_id": product_id,
            "spid": spid,
            "reviews_count": 0,
            "listing_reviews_count": max_reviews or 0,
            "rating_average": 0,
            "stars": {},
            "last_page": 0,
            "requested_pages": max_pages,
            "fetched_pages": 0,
            "fetched_requests": 0,
            "per_page": REVIEWS_PER_PAGE,
            "filtered_mismatch_count": 0,
            "crawl_buckets": list(REVIEW_CRAWL_BUCKETS),
            "crawl_limit_note": "",
        }

        for bucket in REVIEW_CRAWL_BUCKETS:
            bucket_pages = max_pages
            if bucket != "all" and summary["stars"]:
                bucket_total = _star_bucket_count(summary["stars"], bucket)
                if bucket_total <= 0:
                    continue
                bucket_pages = min(max_pages, max(1, (bucket_total + REVIEWS_PER_PAGE - 1) // REVIEWS_PER_PAGE))

            for page in range(1, bucket_pages + 1):
                params = {
                    "product_id": product_id,
                    "limit": REVIEWS_PER_PAGE,
                    "page": page,
                    "include": "comments,contribute_info,attribute_vote_summary",
                    "sort": f"score|desc,id|desc,stars|{bucket}",
                }
                if spid:
                    params["spid"] = spid
                response = self.session.get(TIKI_REVIEW_API, params=params, timeout=self.timeout)
                requests_made += 1
                response.raise_for_status()
                response.encoding = "utf-8"
                data = response.json()

                if bucket == "all" and page == 1:
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

                for raw in raw_reviews:
                    if not _raw_review_matches_selected(raw, product_id=product_id, spid=spid):
                        summary["filtered_mismatch_count"] += 1
                        continue
                    review = _parse_review(raw, product_id)
                    review_id = str(review.get("review_id") or "")
                    if review_id and review_id in seen_review_ids:
                        continue
                    if review_id:
                        seen_review_ids.add(review_id)
                    reviews.append(review)
                summary["fetched_pages"] = max(summary["fetched_pages"], page)

                if max_reviews is not None and len(reviews) >= max_reviews:
                    reviews = reviews[:max_reviews]
                    summary["fetched_requests"] = requests_made
                    return {"summary": _finalize_review_summary(summary, reviews, max_reviews), "reviews": reviews}

                paging = data.get("paging") or {}
                if page >= int(paging.get("last_page") or 1):
                    break

                time.sleep(CRAWL_PAGE_DELAY_SECONDS)

        summary["fetched_requests"] = requests_made
        return {"summary": _finalize_review_summary(summary, reviews, max_reviews), "reviews": reviews}


def _finalize_review_summary(
    summary: dict[str, Any],
    reviews: list[dict[str, Any]],
    max_reviews: int | None,
) -> dict[str, Any]:
    if max_reviews is not None and int(summary.get("reviews_count") or 0) > max_reviews:
        summary["crawl_limit_note"] = (
            "API review public tra tong gop lon hon so review cua listing dang chon; "
            "app da gioi han theo so review listing de tranh lech."
        )
    elif summary.get("reviews_count") and len(reviews) < int(summary.get("reviews_count") or 0):
        summary["crawl_limit_note"] = "Tiki bao nhieu review hon so trang public API hien tra ve."
    return summary


def _star_bucket_count(stars: Any, bucket: str) -> int:
    if not isinstance(stars, dict):
        return 0
    candidates = (
        bucket,
        str(bucket),
        f"{bucket}_star",
        f"{bucket}_stars",
        f"star_{bucket}",
        f"stars_{bucket}",
    )
    for key in candidates:
        value = stars.get(key)
        if isinstance(value, dict):
            count = _first_int(value.get("count"), value.get("total"))
            if count:
                return count
        count = _first_int(value)
        if count:
            return count

    for value in stars.values():
        if not isinstance(value, dict):
            continue
        star_value = str(value.get("star") or value.get("stars") or value.get("rating") or "")
        if star_value == str(bucket):
            count = _first_int(value.get("count"), value.get("total"))
            if count:
                return count
    return 0


def _first_int(*values: Any) -> int:
    for value in values:
        try:
            if value is None or value == "":
                continue
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _product_record_from_payload(item: dict[str, Any]) -> dict[str, Any]:
    url_path = item.get("url_path") or ""
    current_seller = item.get("current_seller") or {}
    quantity_sold = item.get("quantity_sold") or {}
    images = item.get("images") or []
    thumbnail_url = item.get("thumbnail_url") or item.get("thumbnailUrl") or ""
    if not thumbnail_url and images:
        thumbnail_url = images[0].get("base_url") or images[0].get("large_url") or ""

    return {
        "product_id": str(item.get("id") or item.get("product_id") or ""),
        "spid": str(
            item.get("seller_product_id")
            or item.get("spid")
            or current_seller.get("product_id")
            or current_seller.get("seller_product_id")
            or ""
        ),
        "name": item.get("name") or "",
        "brand": item.get("brand_name") or (item.get("brand") or {}).get("name") or "",
        "price": _first_int(item.get("price"), current_seller.get("price")),
        "rating_average": item.get("rating_average") or 0,
        "review_count": _first_int(item.get("review_count"), item.get("reviews_count")),
        "quantity_sold": quantity_sold.get("text", "") if isinstance(quantity_sold, dict) else str(quantity_sold or ""),
        "thumbnail_url": thumbnail_url,
        "url": f"https://tiki.vn/{url_path}" if url_path else "",
    }


def _merge_product_records(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if key == "review_count":
            merged[key] = max(_first_int(merged.get(key)), _first_int(value))
        elif key == "rating_average":
            merged[key] = value or merged.get(key) or 0
        elif key == "price":
            merged[key] = _first_int(value, merged.get(key))
        elif key == "quantity_sold":
            merged[key] = value or merged.get(key) or ""
        elif value not in ("", None, 0):
            merged[key] = value
        elif key not in merged:
            merged[key] = value
    return merged


def _raw_review_matches_selected(raw: dict[str, Any], product_id: str, spid: str = "") -> bool:
    """Reject reviews that Tiki marks as belonging to another concrete listing."""
    raw_spid = str(
        raw.get("spid")
        or raw.get("seller_product_id")
        or raw.get("seller_product", {}).get("id")
        or ""
    ).strip()

    if spid and raw_spid:
        return raw_spid == str(spid)

    raw_product_id = str(raw.get("product_id") or raw.get("product", {}).get("id") or "").strip()
    if raw_product_id and raw_product_id != str(product_id):
        return False

    return True


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").lower().replace("đ", "d"))
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_text)).strip()


def _query_tokens(query: str) -> list[str]:
    tokens = [token for token in _normalize_text(query).split() if len(token) >= 2]
    return [token for token in tokens if token not in QUERY_STOPWORDS]


def _search_categories_for_query(query: str, category: str) -> list[str]:
    configured = list(SEARCH_CATEGORY_GROUPS.get(category, (category,)))
    if category:
        return _dedupe_keep_order(configured)

    normalized = _normalize_text(query)
    if any(_contains_token_or_phrase(normalized, term) for term in PRIMARY_PHONE_TERMS):
        return ["", "1789", "1882", "1815"]
    if any(_contains_token_or_phrase(normalized, term) for term in ("ao", "quan", "vay", "dam", "thoi trang")):
        return ["", "915", "1686"]
    if any(_contains_token_or_phrase(normalized, term) for term in ("laptop", "macbook", "may tinh")):
        return ["", "1846", "1882", "1815"]
    return _dedupe_keep_order(configured)


def _dedupe_keep_order(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value or "")
        if clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _contains_token_or_phrase(name: str, term: str) -> bool:
    term = _normalize_text(term)
    if not term:
        return False
    if " " in term:
        return f" {term} " in f" {name} "
    return term in set(name.split())


def _product_match_score(query: str, product: dict[str, Any], category: str = "") -> int:
    normalized_query = _normalize_text(query)
    name = _normalize_text(f"{product.get('name') or ''} {product.get('brand') or ''}")
    if not normalized_query or not name:
        return 0

    tokens = _query_tokens(query)
    matched_tokens = sum(1 for token in tokens if _contains_token_or_phrase(name, token))
    coverage = matched_tokens / max(len(tokens), 1)
    score = int(coverage * 70)
    if _contains_token_or_phrase(name, normalized_query):
        score += 35
    if name.startswith(normalized_query):
        score += 15
    if category in CATEGORY_REQUIRED_TERMS and any(
        _contains_token_or_phrase(name, term) for term in CATEGORY_REQUIRED_TERMS[category]
    ):
        score += 20
    return score


def _review_count_score(product: dict[str, Any]) -> int:
    count = int(product.get("review_count") or 0)
    if count >= 1000:
        return 5
    if count >= 300:
        return 4
    if count >= 100:
        return 3
    if count >= 30:
        return 2
    if count > 0:
        return 1
    return 0


def _demo_rank_bucket(query: str, product: dict[str, Any]) -> int:
    normalized_query = _normalize_text(query)
    name = _normalize_text(f"{product.get('name') or ''} {product.get('brand') or ''}")
    tokens = _query_tokens(query)
    if not normalized_query or not name:
        return 0
    penalty = _accessory_mismatch_penalty(query, product)
    if _contains_token_or_phrase(name, normalized_query):
        return max(0, 4 - penalty * 2)
    matched_tokens = sum(1 for token in tokens if _contains_token_or_phrase(name, token))
    if tokens and matched_tokens == len(tokens):
        return max(0, 3 - penalty * 2)
    if len(tokens) > 1 and matched_tokens >= len(tokens) - 1:
        return max(0, 2 - penalty * 2)
    if matched_tokens:
        return max(0, 1 - penalty * 2)
    return 0


ACCESSORY_TERMS = (
    "op lung",
    "cuong luc",
    "kinh cuong luc",
    "mieng dan",
    "sac",
    "cap",
    "day sac",
    "tai nghe",
    "bao da",
    "gia do",
    "de do",
)

PRIMARY_PHONE_TERMS = (
    "iphone",
    "samsung",
    "xiaomi",
    "oppo",
    "vivo",
    "realme",
    "dien thoai",
    "smartphone",
)


def _accessory_mismatch_penalty(query: str, product: dict[str, Any]) -> int:
    normalized_query = _normalize_text(query)
    name = _normalize_text(f"{product.get('name') or ''} {product.get('brand') or ''}")
    if not any(_contains_token_or_phrase(normalized_query, term) for term in PRIMARY_PHONE_TERMS):
        return 0
    if any(_contains_token_or_phrase(normalized_query, term) for term in ACCESSORY_TERMS):
        return 0
    return 1 if any(_contains_token_or_phrase(name, term) for term in ACCESSORY_TERMS) else 0


def _primary_product_score(query: str, product: dict[str, Any]) -> int:
    normalized_query = _normalize_text(query)
    name = _normalize_text(f"{product.get('name') or ''} {product.get('brand') or ''}")
    if not any(_contains_token_or_phrase(normalized_query, term) for term in PRIMARY_PHONE_TERMS):
        return 0
    if _accessory_mismatch_penalty(query, product):
        return 0
    score = 1
    if _contains_token_or_phrase(name, "apple") or _contains_token_or_phrase(name, "iphone"):
        score += 1
    if name.startswith("apple iphone") or name.startswith("dien thoai iphone"):
        score += 2
    return score


def _is_relevant_product_match(query: str, product: dict[str, Any], category: str = "") -> bool:
    name = _normalize_text(f"{product.get('name') or ''} {product.get('brand') or ''}")
    tokens = _query_tokens(query)
    if not tokens:
        return True

    matched_tokens = sum(1 for token in tokens if _contains_token_or_phrase(name, token))
    if _contains_token_or_phrase(name, _normalize_text(query)):
        query_ok = True
    elif len(tokens) == 1:
        query_ok = matched_tokens == 1
    else:
        query_ok = matched_tokens >= max(2, len(tokens) - 1)

    if category in CATEGORY_REQUIRED_TERMS:
        category_ok = any(_contains_token_or_phrase(name, term) for term in CATEGORY_REQUIRED_TERMS[category])
        return query_ok and category_ok
    return query_ok


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
