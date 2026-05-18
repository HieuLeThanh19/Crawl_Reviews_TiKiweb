"""
FastAPI app for searching real Tiki products, crawling public reviews, and
showing saved review data.
"""
from __future__ import annotations

import os
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime
from typing import Any, Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from requests import RequestException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import API_MAX_LIMIT, MAX_CRAWL_PAGES, REVIEWS_PER_PAGE
from crawler.tiki_client import TikiClient, TikiClientError
from storage import sqlite_store

_db_conn = None  # Kept for older tests that monkeypatch api.app._db_conn.
MIN_LOCAL_REVIEWS_FOR_AI_SUMMARY = 5

app = FastAPI(
    title="Tiki Review System",
    description="Search real Tiki products, crawl public reviews, and inspect review data.",
    version="2.0.0",
    docs_url="/api/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CrawlRequest(BaseModel):
    product_id: str
    max_pages: int = Field(default=0, ge=0, le=MAX_CRAWL_PAGES)
    replace_existing: bool = True
    name: Optional[str] = None
    spid: Optional[str] = None
    url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    price: Optional[int] = 0
    rating_average: Optional[float] = 0
    review_count: Optional[int] = 0


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    product_id: str = ""
    history: list[ChatMessage] = []
    message: str


def _gemini_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key:
        return key

    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        with open(env_path, encoding="utf-8") as env_file:
            for line in env_file:
                clean = line.strip()
                if clean.startswith("GEMINI_API_KEY="):
                    return clean.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _get_gemini_model(system_instruction: str | None = None):
    api_key = _gemini_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="Chua cau hinh GEMINI_API_KEY trong file .env")

    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Chua cai thu vien google-generativeai. Hay chay: pip install google-generativeai",
        ) from exc

    genai.configure(api_key=api_key)
    kwargs = {"system_instruction": system_instruction} if system_instruction else {}
    model_name = os.getenv("GEMINI_MODEL", "").strip()
    if not model_name:
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        try:
            with open(env_path, encoding="utf-8") as env_file:
                for line in env_file:
                    clean = line.strip()
                    if clean.startswith("GEMINI_MODEL="):
                        model_name = clean.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except OSError:
            pass
    return genai.GenerativeModel(model_name or "gemini-2.5-flash", **kwargs)


def _gemini_model_name() -> str:
    model_name = os.getenv("GEMINI_MODEL", "").strip()
    if model_name:
        return model_name
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        with open(env_path, encoding="utf-8") as env_file:
            for line in env_file:
                clean = line.strip()
                if clean.startswith("GEMINI_MODEL="):
                    return clean.split("=", 1)[1].strip().strip('"').strip("'") or "gemini-2.5-flash"
    except OSError:
        pass
    return "gemini-2.5-flash"


def _gemini_generate_text(
    message: str,
    system_instruction: str | None = None,
    history: list[dict[str, Any]] | None = None,
    response_mime_type: str | None = None,
) -> str:
    api_key = _gemini_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="Chua cau hinh GEMINI_API_KEY trong file .env")

    model_name = _gemini_model_name().removeprefix("models/")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    contents = list(history or [])
    contents.append({"role": "user", "parts": [{"text": message}]})
    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.35,
            "maxOutputTokens": 2000,
        },
    }
    if response_mime_type:
        payload["generationConfig"]["responseMimeType"] = response_mime_type
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    try:
        session = requests.Session()
        session.trust_env = False
        response = session.post(
            url,
            params={"key": api_key},
            json=payload,
            timeout=int(os.getenv("GEMINI_TIMEOUT", "25")),
        )
        data = response.json()
    except requests.Timeout as exc:
        raise HTTPException(status_code=504, detail="Gemini phan hoi qua lau, hay thu lai.") from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Khong ket noi duoc Gemini. Hay kiem tra internet/proxy tren may.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Gemini tra ve du lieu khong hop le") from exc

    if response.status_code >= 400:
        detail = data.get("error", {}).get("message") if isinstance(data, dict) else response.text
        raise HTTPException(status_code=response.status_code, detail=f"Loi Gemini API: {detail or response.text}")

    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "\n".join(part.get("text", "") for part in parts).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="Gemini khong tra ve noi dung tra loi") from exc


def _review_text(review: dict[str, Any], max_len: int = 280) -> str:
    text = " ".join(
        str(review.get(key) or "").strip()
        for key in ("title", "content")
        if str(review.get(key) or "").strip()
    )
    return _truncate_text(text, max_len=max_len)


def _truncate_text(text: str, max_len: int = 160) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= max_len:
        return clean

    sentence_cut = max(
        clean.rfind(".", 0, max_len),
        clean.rfind("!", 0, max_len),
        clean.rfind("?", 0, max_len),
        clean.rfind(";", 0, max_len),
    )
    if sentence_cut >= max(45, max_len // 2):
        return clean[: sentence_cut + 1].strip()

    word_cut = clean.rfind(" ", 0, max_len)
    if word_cut < max(35, max_len // 2):
        word_cut = max_len
    return clean[:word_cut].rstrip(" ,;:-") + "..."


def _reviews_with_text(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [review for review in reviews if _review_text(review, max_len=700)]


def _normalize_for_keyword(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower().replace("đ", "d"))
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_text)).strip()


def _looks_like_no_data(text: str) -> bool:
    normalized = _normalize_for_keyword(text)
    no_data_markers = (
        "chua du du lieu",
        "khong du du lieu",
        "chua thay",
        "chua co du",
        "khong co du",
        "khong co nhan xet",
    )
    return any(marker in normalized for marker in no_data_markers)


SERVICE_KEYWORDS = (
    "giao", "giao hang", "nhan hang", "ship", "shipper", "van chuyen",
    "dong goi", "dong hang", "goi hang", "bao bi", "vo hop",
    "tiki", "shop", "nha ban", "nguoi ban", "seller", "tu van",
    "hoa don", "bao hanh", "doi tra", "tra hang", "hoan tien", "khieu nai",
    "khach hang", "cham soc", "ho tro", "nhan vien", "phan hoi", "xu ly",
    "cho lau", "giao cham", "giao nhanh", "viettel", "ghn", "ghtk", "j t",
)
POSITIVE_PRODUCT_KEYWORDS = (
    "dep",
    "xinh",
    "de thuong",
    "nhu hinh",
    "dung mo ta",
    "tot",
    "chat luong",
    "ung y",
    "hai long",
    "chac chan",
    "tien loi",
    "gon",
    "nhe",
    "on ap",
    "ok",
)

GENERIC_POSITIVE_TEXT = (
    "cuc ki hai long",
    "rat hai long",
    "hai long",
    "ok",
)


def _has_service_signal(review: dict[str, Any]) -> bool:
    text = _normalize_for_keyword(_review_text(review, max_len=700))
    return any(keyword in text for keyword in SERVICE_KEYWORDS)


def _reviews_for_ratings(product_id: str, ratings: list[int], limit_each: int = 60) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rating in ratings:
        data = sqlite_store.list_reviews(
            product_id=product_id,
            page=1,
            limit=limit_each,
            rating=rating,
            sort="created_at_desc",
        )
        for review in data.get("items") or []:
            review_id = str(review.get("review_id") or "")
            if review_id and review_id in seen:
                continue
            if review_id:
                seen.add(review_id)
            items.append(review)
    return items


def _compact_review_lines(reviews: list[dict[str, Any]], max_items: int = 18) -> str:
    lines = []
    for review in reviews[:max_items]:
        text = _review_text(review, max_len=220)
        if text:
            lines.append(f"- {review.get('rating', 0)}/5: {text}")
    return "\n".join(lines) if lines else "Không có review phù hợp."


def _positive_snippets(reviews: list[dict[str, Any]], limit: int = 5) -> list[str]:
    snippets: list[str] = []
    seen: set[str] = set()
    for review in reviews:
        content = str(review.get("content") or "").strip()
        title = str(review.get("title") or "").strip()
        text = _truncate_text(content or title, max_len=140)
        normalized = _normalize_for_keyword(text)
        if not text or normalized in GENERIC_POSITIVE_TEXT:
            continue
        if not any(keyword in normalized for keyword in POSITIVE_PRODUCT_KEYWORDS):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        snippets.append(text)
        if len(snippets) >= limit:
            break
    return snippets


def _fallback_positive_summary(reviews: list[dict[str, Any]], positive_total: int) -> str:
    snippets = _positive_snippets(reviews, limit=4)
    if snippets:
        return "Một số review tích cực nhắc đến: " + "; ".join(snippets) + "."
    if positive_total > 0:
        return f"Có {positive_total} review 4-5 sao; người mua nhìn chung hài lòng với sản phẩm."
    return ""


def _fallback_negative_summary(reviews: list[dict[str, Any]]) -> str:
    snippets = []
    for review in reviews:
        text = _review_text(review, max_len=180)
        if text:
            snippets.append(text)
        if len(snippets) >= 3:
            break
    if not snippets:
        return ""
    return "Một số đánh giá thấp nhắc đến: " + "; ".join(snippets) + "."


def _fallback_service_summary(reviews: list[dict[str, Any]], positive: bool) -> str:
    snippets = []
    for review in reviews:
        text = _review_text(review, max_len=190)
        if text:
            snippets.append(text)
        if len(snippets) >= 3:
            break
    if not snippets:
        return ""
    prefix = "Một số review khen dịch vụ/giao hàng: " if positive else "Một số review chê dịch vụ/giao hàng: "
    return prefix + "; ".join(snippets) + "."


def _looks_incomplete_summary(text: str) -> bool:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return True
    normalized = _normalize_for_keyword(clean)
    weak_prefixes = (
        "san pham duoc danh",
        "duoc danh gia",
        "nhin chung duoc danh",
        "review hien co chua",
    )
    if any(normalized == phrase or normalized.startswith(phrase) for phrase in weak_prefixes):
        return True
    if len(clean) < 28:
        return True
    if clean.endswith((",", ";", ":", "-", "…", "...")):
        return True
    words = normalized.split()
    last_word = words[-1] if words else ""
    return last_word in {"va", "voi", "duoc", "bi", "la", "co", "nhu", "ve", "tu"}


def _sanitize_user_ai_reply(text: str) -> str:
    clean = str(text or "")
    replacements = (
        (r"\breview local\b", "đánh giá"),
        (r"\bđánh giá local\b", "đánh giá"),
        (r"\bdữ liệu review local\b", "dữ liệu đánh giá"),
        (r"\bdữ liệu local\b", "dữ liệu đánh giá"),
        (r"\blocal\b", ""),
        (r"không có đủ dữ liệu đánh giá", "mình chưa thấy đủ thông tin trong các đánh giá"),
        (r"không đủ dữ liệu đánh giá để trả lời/tổng hợp", "mình chưa thấy đủ thông tin trong các đánh giá để trả lời"),
        (r"không đủ dữ liệu đánh giá để trả lời", "mình chưa thấy đủ thông tin trong các đánh giá để trả lời"),
    )
    for pattern, replacement in replacements:
        clean = re.sub(pattern, replacement, clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bmình\s+mình\b", "mình", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s{2,}", " ", clean)
    clean = re.sub(r"\s+([,.!?;:])", r"\1", clean)
    return clean.strip()


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        cleaned = match.group(0)
    return json.loads(cleaned)


def _extract_summary(raw_text: str) -> dict[str, Any]:
    try:
        return _extract_json_object(raw_text)
    except (json.JSONDecodeError, ValueError):
        pass

    keys = {
        "overall_verdict",
        "product_positive_count",
        "product_negative_count",
        "product_positive_summary",
        "product_negative_summary",
        "service_positive_count",
        "service_negative_count",
        "service_positive_summary",
        "service_negative_summary",
        "buyer_advice",
    }
    parsed: dict[str, Any] = {}
    for line in raw_text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        clean_key = key.strip().strip("-* ").lower()
        if clean_key not in keys:
            continue
        clean_value = value.strip().strip('"')
        if clean_key.endswith("_count"):
            match = re.search(r"\d+", clean_value)
            parsed[clean_key] = int(match.group(0)) if match else 0
        else:
            parsed[clean_key] = clean_value

    if not parsed:
        raise json.JSONDecodeError("Cannot parse Gemini summary", raw_text, 0)

    defaults: dict[str, Any] = {
        "overall_verdict": "",
        "product_positive_count": 0,
        "product_negative_count": 0,
        "product_positive_summary": "",
        "product_negative_summary": "",
        "service_positive_count": 0,
        "service_negative_count": 0,
        "service_positive_summary": "",
        "service_negative_summary": "",
        "buyer_advice": "",
    }
    return {**defaults, **parsed}


def _product_review_context(product_id: str, review_limit: int = 100) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    product = sqlite_store.get_product(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Chua co san pham nay trong du lieu local")

    reviews_data = sqlite_store.list_reviews(
        product_id=product_id,
        page=1,
        limit=review_limit,
        sort="created_at_desc",
    )
    reviews = reviews_data.get("items") or []
    review_lines = []
    for review in reviews:
        text = _review_text(review)
        if text:
            review_lines.append(f"- {review.get('rating', 0)}/5: {text}")

    context = "\n".join(review_lines) if review_lines else "Chua co review local nao cho san pham nay."
    return product, reviews, context


def _ensure_enough_reviews_for_summary(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    usable_reviews = _reviews_with_text(reviews)
    if len(usable_reviews) < MIN_LOCAL_REVIEWS_FOR_AI_SUMMARY:
        raise HTTPException(
            status_code=422,
            detail=(
                "Không đủ dữ liệu review local để tổng hợp. "
                f"Cần ít nhất {MIN_LOCAL_REVIEWS_FOR_AI_SUMMARY} review có nội dung, "
                f"hiện chỉ có {len(usable_reviews)}."
            ),
        )
    return usable_reviews


@app.on_event("startup")
def startup() -> None:
    try:
        sqlite_store.init_db()
    except sqlite3.Error:
        # Keep search/UI available even if SQLite is temporarily locked by OneDrive or another process.
        pass


@app.get("/api/health")
def health_check():
    db_error = None
    total_reviews = 0
    try:
        sqlite_store.init_db()
        total_reviews = sqlite_store.count_reviews()
    except sqlite3.Error as exc:
        db_error = str(exc)
    return {
        "status": "ok" if db_error is None else "degraded",
        "db": "sqlite",
        "db_path": str(sqlite_store.DB_PATH),
        "total_reviews": total_reviews,
        "db_error": db_error,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/tiki/search")
def search_tiki_products(
    q: str = Query(..., min_length=2, description="Tên sản phẩm cần tìm trên Tiki"),
    limit: int = Query(10, ge=1, le=40),
    category: str = Query("", description="Category Tiki, bỏ trống để tìm tất cả"),
    pages: int = Query(5, ge=1, le=25, description="Số trang kết quả Tiki cần gom"),
):
    """Search real Tiki products and return product ids visible to the user."""
    try:
        products = TikiClient().search_products(q, limit=limit, category=category, pages=pages)
        try:
            sqlite_store.save_products(products)
        except sqlite3.Error:
            # Search should still be usable when OneDrive or another process briefly locks SQLite.
            pass
        return {"query": q, "total": len(products), "pages": pages, "items": products}
    except RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Không gọi được Tiki search API: {exc}") from exc


@app.post("/api/tiki/crawl")
def crawl_tiki_reviews(payload: CrawlRequest):
    """Crawl public reviews for a selected Tiki product and save them to SQLite."""
    product_id = payload.product_id.strip()
    if not product_id:
        raise HTTPException(status_code=422, detail="product_id không được rỗng")

    product = {
        "product_id": product_id,
        "spid": payload.spid or "",
        "name": payload.name or f"Tiki product {product_id}",
        "brand": "",
        "price": payload.price or 0,
        "rating_average": payload.rating_average or 0,
        "review_count": payload.review_count or 0,
        "quantity_sold": "",
        "thumbnail_url": payload.thumbnail_url or "",
        "url": payload.url or "",
    }
    client = TikiClient()
    try:
        detail = client.get_product_detail(product_id, spid=payload.spid)
        product.update({key: value for key, value in detail.items() if value not in ("", None, 0)})
        if payload.spid and not product.get("spid"):
            product["spid"] = payload.spid
        if payload.url and not product.get("url"):
            product["url"] = payload.url
    except (RequestException, TikiClientError, ValueError):
        # Keep crawling usable even when the detail endpoint is temporarily unavailable.
        pass
    sqlite_store.save_product(product)

    try:
        max_reviews = int(product.get("review_count") or payload.review_count or 0) or None
        if payload.max_pages <= 0 and max_reviews:
            effective_max_pages = min(MAX_CRAWL_PAGES, max(1, (max_reviews + REVIEWS_PER_PAGE - 1) // REVIEWS_PER_PAGE))
        elif payload.max_pages <= 0:
            effective_max_pages = MAX_CRAWL_PAGES
        else:
            effective_max_pages = payload.max_pages

        result = client.fetch_reviews(
            product_id,
            max_pages=effective_max_pages,
            max_reviews=max_reviews,
            spid=product.get("spid") or payload.spid,
        )
    except (RequestException, TikiClientError) as exc:
        raise HTTPException(status_code=502, detail=f"Không crawl được review từ Tiki: {exc}") from exc

    deleted = sqlite_store.delete_reviews_for_product(product_id) if payload.replace_existing else 0
    inserted = sqlite_store.save_reviews(product_id, result["reviews"])
    stats = sqlite_store.product_stats(product_id)
    return {
        "product_id": product_id,
        "max_pages": effective_max_pages,
        "requested_max_pages": payload.max_pages,
        "replace_existing": payload.replace_existing,
        "fetched": len(result["reviews"]),
        "deleted": deleted,
        "inserted": inserted,
        "summary_from_tiki": result["summary"],
        "listing_review_count": max_reviews or 0,
        "reachable_pages": result["summary"].get("last_page") or result["summary"].get("fetched_pages") or 0,
        "saved_stats": stats,
    }


@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    product = sqlite_store.get_product(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Chưa có sản phẩm này trong dữ liệu local")
    return product


@app.get("/api/products/{product_id}/reviews")
def get_product_reviews(
    product_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=API_MAX_LIMIT),
    rating: Optional[int] = Query(None, ge=1, le=5),
    classification: Optional[str] = Query(None, pattern="^(good|neutral|bad)$"),
    sort: str = Query("created_at_desc"),
):
    """Return saved reviews. Crawl first if this list is empty."""
    return sqlite_store.list_reviews(
        product_id=product_id,
        page=page,
        limit=limit,
        rating=rating,
        classification=classification,
        sort=sort,
    )


@app.get("/api/reviews/{review_id}")
def get_review(review_id: str):
    review = sqlite_store.get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Không tìm thấy đánh giá")
    return review


@app.get("/api/analytics/rating-distribution")
def get_rating_distribution(product_id: str = Query(...)):
    stats = sqlite_store.product_stats(product_id)
    return {"product_id": product_id, "distribution": stats["distribution"]}


@app.get("/api/analytics/summary")
def get_summary(product_id: str = Query(...)):
    product = sqlite_store.get_product(product_id)
    stats = sqlite_store.product_stats(product_id)
    return {"product": product, "stats": stats}


@app.get("/api/products/{product_id}/ai-summary")
def get_ai_summary(product_id: str):
    """Summarize saved reviews for one product with Gemini."""
    product, reviews, reviews_context = _product_review_context(product_id, review_limit=150)
    if not reviews:
        raise HTTPException(status_code=404, detail="Chưa có review local. Hãy crawl review trước khi dùng AI.")
    _ensure_enough_reviews_for_summary(reviews)

    stats = sqlite_store.product_stats(product_id)
    distribution = stats.get("distribution") or {}
    positive_total = int(distribution.get("4") or 0) + int(distribution.get("5") or 0)
    neutral_total = int(distribution.get("3") or 0)
    negative_total = int(distribution.get("1") or 0) + int(distribution.get("2") or 0)

    bad_reviews = _reviews_for_ratings(product_id, [1, 2], limit_each=80)
    good_reviews = _reviews_for_ratings(product_id, [5, 4], limit_each=200)
    neutral_reviews = _reviews_for_ratings(product_id, [3], limit_each=40)
    service_bad_reviews = [review for review in bad_reviews if _has_service_signal(review)]
    product_bad_reviews = [review for review in bad_reviews if not _has_service_signal(review)]
    if bad_reviews and not product_bad_reviews:
        product_bad_reviews = bad_reviews[:8]
    service_good_reviews = [review for review in good_reviews if _has_service_signal(review)]
    product_positive_fallback = _fallback_positive_summary(good_reviews, positive_total)
    product_positive_snippets = _positive_snippets(good_reviews, limit=8)

    prompt = f"""
Bạn là trợ lý AI phân tích review sản phẩm Tiki bằng tiếng Việt có dấu.
Chỉ được tóm tắt từ review local đã crawl bên dưới. Không dùng kiến thức ngoài, không tự suy diễn, không bịa thông tin.
Phải đọc kỹ nhóm review 1-2 sao. Nếu có review tệ, bắt buộc nêu rõ điểm bị chê.
Đồng thời phải đọc review 4-5 sao và không được nói "chưa đủ dữ liệu" cho điểm tích cực nếu thống kê local có review 4-5 sao.
Tách rõ nhận xét về sản phẩm và nhận xét về dịch vụ. Dịch vụ gồm: giao hàng, vận chuyển, đóng gói, shop, Tiki, bảo hành, đổi trả, hỗ trợ, phản hồi.

Sản phẩm:
- Tên: {product.get("name") or product_id}
- Product ID: {product_id}
- Giá: {product.get("price") or 0} VND
- Sao Tiki báo: {product.get("rating_average") or 0}/5
- Số review Tiki báo: {product.get("review_count") or 0}
- Số review local đang phân tích: {len(reviews)}
- Sao trung bình local: {stats.get("avg_rating", 0)}/5
- Thống kê local theo sao: {distribution}
- Local tích cực 4-5 sao: {positive_total}
- Local trung lập 3 sao: {neutral_total}
- Local tiêu cực 1-2 sao: {negative_total}

Review mới nhất:
{reviews_context}

Review tiêu cực 1-2 sao cần đọc kỹ:
{_compact_review_lines(bad_reviews, max_items=24)}

Review tích cực 4-5 sao để đối chiếu:
{_compact_review_lines(good_reviews, max_items=18)}

Cụm tích cực app đã bắt được:
{_compact_review_lines([{"rating": 5, "title": text, "content": ""} for text in product_positive_snippets], max_items=8)}

Review trung lập 3 sao:
{_compact_review_lines(neutral_reviews, max_items=8)}

Trả về đúng 10 dòng theo format key: value, không markdown, không danh sách thêm:
overall_verdict: 1 câu kết luận chung về sản phẩm
product_positive_count: đúng số {positive_total}
product_negative_count: đúng số review 1-2 sao liên quan sản phẩm, tối thiểu {len(product_bad_reviews)} nếu có review tệ
product_positive_summary: 1 câu ngắn về điểm mạnh sản phẩm; nếu thấy các từ như đẹp/như hình/dễ thương/tốt thì phải nhắc đúng ý đó
product_negative_summary: 1 câu ngắn về điểm yếu/rủi ro của sản phẩm; nếu có review 1-2 sao thì không được để trống
service_positive_count: đúng số review tích cực có nhắc dịch vụ, gợi ý {len(service_good_reviews)}
service_negative_count: đúng số review tiêu cực có nhắc dịch vụ, gợi ý {len(service_bad_reviews)}
service_positive_summary: 1 câu ngắn về giao hàng/đóng gói/dịch vụ nếu có
service_negative_summary: 1 câu ngắn về vấn đề dịch vụ nếu có
buyer_advice: 1 câu khuyến nghị mua hàng cân bằng
"""
    try:
        response_text = _gemini_generate_text(prompt)
        summary = _extract_summary(response_text)
        summary["product_positive_count"] = positive_total
        summary["product_negative_count"] = len(product_bad_reviews)
        summary["service_positive_count"] = len(service_good_reviews)
        summary["service_negative_count"] = len(service_bad_reviews)
        if product_positive_fallback and (
            not summary.get("product_positive_summary")
            or _looks_like_no_data(str(summary.get("product_positive_summary") or ""))
            or _looks_incomplete_summary(str(summary.get("product_positive_summary") or ""))
        ):
            summary["product_positive_summary"] = product_positive_fallback
        if product_bad_reviews and (
            not summary.get("product_negative_summary")
            or _looks_incomplete_summary(str(summary.get("product_negative_summary") or ""))
        ):
            summary["product_negative_summary"] = _fallback_negative_summary(product_bad_reviews)
        if service_good_reviews and (
            not summary.get("service_positive_summary")
            or _looks_like_no_data(str(summary.get("service_positive_summary") or ""))
            or _looks_incomplete_summary(str(summary.get("service_positive_summary") or ""))
        ):
            summary["service_positive_summary"] = _fallback_service_summary(service_good_reviews, positive=True)
        if service_bad_reviews and (
            not summary.get("service_negative_summary")
            or _looks_incomplete_summary(str(summary.get("service_negative_summary") or ""))
        ):
            summary["service_negative_summary"] = _fallback_service_summary(service_bad_reviews, positive=False)
        return {
            "product_id": product_id,
            "generated_at": datetime.utcnow().isoformat(),
            "summary": summary,
        }
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini tra ve tom tat khong hop le: {exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Loi Gemini API: {exc}") from exc


@app.post("/api/chat")
def chat_with_ai(payload: ChatRequest):
    """Chat with Gemini using the selected product and saved reviews as context."""
    product_id = payload.product_id.strip()
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="message khong duoc rong")

    product: dict[str, Any] = {}
    reviews_context = "Chua co san pham duoc chon."
    stats: dict[str, Any] = {"total": 0, "avg_rating": 0}
    selected_reviews: list[dict[str, Any]] = []
    if product_id:
        product, selected_reviews, reviews_context = _product_review_context(product_id, review_limit=90)
        stats = sqlite_store.product_stats(product_id)
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    system_instruction = f"""
Ban ten la "Tro ly AI". Ban ho tro nguoi dung tim hieu san pham Tiki dang xem.
Ban chi duoc dung thong tin san pham va cac danh gia trong context khi tra loi ve san pham.
Khi tra loi cho nguoi dung, tuyet doi khong dung cac cum "local", "review local", "du lieu local".
Neu khong thay thong tin trong context, hay noi "minh chua thay thong tin nay trong cac danh gia" hoac "cac danh gia hien co chua nhac den".

Thoi gian hien tai tren server: {now_text}.

Thong tin san pham:
- Ten: {product.get("name") or (product_id if product_id else "Chua chon san pham")}
- Product ID: {product_id or "Chua chon"}
- Thuong hieu: {product.get("brand") or "Khong ro"}
- Gia: {product.get("price") or 0} VND
- Sao Tiki bao: {product.get("rating_average") or 0}/5
- So review Tiki bao: {product.get("review_count") or 0}
- So review da doc: {stats.get("total", 0)}
- Sao trung binh tu cac danh gia: {stats.get("avg_rating", 0)}/5
- Link: {product.get("url") or ""}

Danh gia gan nhat:
{reviews_context}

Quy tac:
1. Neu hoi ve chat luong, thong so, do ben, giao hang, gia tri mua hang: chi tra loi dua tren cac danh gia va thong tin san pham trong context.
2. Neu chua co danh gia hoac danh gia khong co du thong tin, noi ro "minh chua thay thong tin nay trong cac danh gia".
3. Neu hoi kien thuc chung nhu ngay gio, khai niem, cach so sanh chung: tra loi ngan gon va keo ve viec ho tro san pham.
4. Neu hoi qua xa chu de san pham va khong huu ich cho viec mua/danh gia san pham: tu choi lich su, moi nguoi dung hoi ve san pham.
5. Khong bia thong so khong co trong context. Khong noi ban da doc internet truc tiep.
6. Tra loi bang tieng Viet, than thien, toi da 200 tu.
"""

    try:
        history_for_gemini = []
        for item in payload.history[-10:]:
            role = "model" if item.role == "model" else "user"
            if item.content.strip():
                history_for_gemini.append({"role": role, "parts": [{"text": item.content.strip()}]})

        if product_id and len(_reviews_with_text(selected_reviews)) < MIN_LOCAL_REVIEWS_FOR_AI_SUMMARY:
            return {
                "product_id": product_id,
                "reply": (
                    "Mình chưa thấy đủ thông tin trong các đánh giá để trả lời chắc chắn cho sản phẩm này. "
                    "Bạn hãy crawl thêm đánh giá rồi hỏi lại nhé."
                ),
            }

        reply = _gemini_generate_text(
            message,
            system_instruction=system_instruction,
            history=history_for_gemini,
        )
        reply = _sanitize_user_ai_reply(reply)
        return {
            "product_id": product_id,
            "reply": reply or "Toi chua co cau tra loi phu hop.",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Loi Gemini API: {exc}") from exc


@app.get("/", response_class=HTMLResponse)
def dashboard():
    with open("ui/dashboard.html", encoding="utf-8") as f:
        return f.read()
