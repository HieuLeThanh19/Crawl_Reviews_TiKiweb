"""
FastAPI app for searching real Tiki products, crawling public reviews, and
showing saved review data.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from requests import RequestException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import API_MAX_LIMIT
from crawler.tiki_client import TikiClient, TikiClientError
from storage import sqlite_store

_db_conn = None  # Kept for older tests that monkeypatch api.app._db_conn.

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
    max_pages: int = Field(default=10, ge=1, le=100)
    replace_existing: bool = True
    name: Optional[str] = None
    spid: Optional[str] = None
    url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    price: Optional[int] = 0
    rating_average: Optional[float] = 0
    review_count: Optional[int] = 0


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
    sqlite_store.save_product(product)

    try:
        max_reviews = payload.review_count if payload.review_count and payload.review_count > 0 else None
        result = TikiClient().fetch_reviews(
            product_id,
            max_pages=payload.max_pages,
            max_reviews=max_reviews,
        )
    except (RequestException, TikiClientError) as exc:
        raise HTTPException(status_code=502, detail=f"Không crawl được review từ Tiki: {exc}") from exc

    deleted = sqlite_store.delete_reviews_for_product(product_id) if payload.replace_existing else 0
    inserted = sqlite_store.save_reviews(product_id, result["reviews"])
    stats = sqlite_store.product_stats(product_id)
    return {
        "product_id": product_id,
        "max_pages": payload.max_pages,
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


@app.get("/", response_class=HTMLResponse)
def dashboard():
    with open("ui/dashboard.html", encoding="utf-8") as f:
        return f.read()
