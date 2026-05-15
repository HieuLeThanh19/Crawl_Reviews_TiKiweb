"""
SQLite persistence for crawled Tiki products and reviews.

This keeps the project easy to run locally while still storing real crawled
data.  PostgreSQL can remain an advanced deployment option, but the UI and API
default to SQLite so the demo works after installing Python dependencies.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = Path("data/tiki_reviews.sqlite3")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                product_id TEXT PRIMARY KEY,
                spid TEXT,
                name TEXT,
                brand TEXT,
                price INTEGER DEFAULT 0,
                rating_average REAL DEFAULT 0,
                review_count INTEGER DEFAULT 0,
                quantity_sold TEXT,
                thumbnail_url TEXT,
                url TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reviews (
                review_id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                user_id TEXT,
                rating INTEGER,
                title TEXT,
                content TEXT,
                created_at TEXT,
                helpful_count INTEGER DEFAULT 0,
                images TEXT DEFAULT '[]',
                sentiment TEXT,
                tokens TEXT DEFAULT '[]',
                topics TEXT DEFAULT '[]',
                crawled_at TEXT NOT NULL,
                raw_status TEXT,
                FOREIGN KEY(product_id) REFERENCES products(product_id)
            );

            CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id);
            CREATE INDEX IF NOT EXISTS idx_reviews_rating ON reviews(product_id, rating);
            CREATE INDEX IF NOT EXISTS idx_reviews_created ON reviews(product_id, created_at DESC);
            """
        )


def save_products(products: list[dict[str, Any]]) -> None:
    if not products:
        return
    init_db()
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO products (
                product_id, spid, name, brand, price, rating_average,
                review_count, quantity_sold, thumbnail_url, url, updated_at
            ) VALUES (
                :product_id, :spid, :name, :brand, :price, :rating_average,
                :review_count, :quantity_sold, :thumbnail_url, :url, :updated_at
            )
            ON CONFLICT(product_id) DO UPDATE SET
                spid=excluded.spid,
                name=excluded.name,
                brand=excluded.brand,
                price=excluded.price,
                rating_average=excluded.rating_average,
                review_count=excluded.review_count,
                quantity_sold=excluded.quantity_sold,
                thumbnail_url=excluded.thumbnail_url,
                url=excluded.url,
                updated_at=excluded.updated_at
            """,
            [{**product, "updated_at": now} for product in products],
        )


def save_product(product: dict[str, Any]) -> None:
    save_products([product])


def delete_reviews_for_product(product_id: str) -> int:
    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM reviews WHERE product_id = ?", (product_id,))
        return cursor.rowcount or 0


def save_reviews(product_id: str, reviews: list[dict[str, Any]]) -> int:
    if not reviews:
        return 0
    init_db()
    inserted = 0
    with _connect() as conn:
        for review in reviews:
            exists = conn.execute(
                "SELECT 1 FROM reviews WHERE review_id = ?",
                (review.get("review_id"),),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO reviews (
                    review_id, product_id, user_id, rating, title, content,
                    created_at, helpful_count, images, sentiment, tokens,
                    topics, crawled_at, raw_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(review_id) DO UPDATE SET
                    product_id=excluded.product_id,
                    user_id=excluded.user_id,
                    rating=excluded.rating,
                    title=excluded.title,
                    content=excluded.content,
                    created_at=excluded.created_at,
                    helpful_count=excluded.helpful_count,
                    images=excluded.images,
                    sentiment=excluded.sentiment,
                    tokens=excluded.tokens,
                    topics=excluded.topics,
                    crawled_at=excluded.crawled_at,
                    raw_status=excluded.raw_status
                """,
                (
                    review.get("review_id"),
                    product_id,
                    review.get("user_id"),
                    review.get("rating"),
                    review.get("title"),
                    review.get("content"),
                    str(review.get("created_at") or ""),
                    review.get("helpful_count") or 0,
                    json.dumps(review.get("images") or [], ensure_ascii=False),
                    review.get("sentiment"),
                    json.dumps(review.get("tokens") or [], ensure_ascii=False),
                    json.dumps(review.get("topics") or [], ensure_ascii=False),
                    datetime.utcnow().isoformat(),
                    review.get("_raw_status"),
                ),
            )
            if not exists:
                inserted += 1
    return inserted


def get_product(product_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE product_id = ?", (product_id,)).fetchone()
    return dict(row) if row else None


def list_reviews(
    product_id: str,
    page: int = 1,
    limit: int = 20,
    rating: int | None = None,
    classification: str | None = None,
    sort: str = "created_at_desc",
) -> dict[str, Any]:
    init_db()
    conditions = ["product_id = ?"]
    params: list[Any] = [product_id]
    if rating:
        conditions.append("rating = ?")
        params.append(rating)
    elif classification == "good":
        conditions.append("rating >= 4")
    elif classification == "neutral":
        conditions.append("rating = 3")
    elif classification == "bad":
        conditions.append("rating <= 2")

    order_map = {
        "created_at_desc": "created_at DESC",
        "created_at_asc": "created_at ASC",
        "rating_desc": "rating DESC",
        "rating_asc": "rating ASC",
        "helpful_desc": "helpful_count DESC",
    }
    order = order_map.get(sort, "created_at DESC")
    where = " AND ".join(conditions)
    offset = (page - 1) * limit

    with _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM reviews WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT review_id, product_id, user_id, rating, title, content,
                   created_at, helpful_count, images, sentiment, tokens,
                   topics, crawled_at, raw_status
            FROM reviews
            WHERE {where}
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": [_decode_review(row) for row in rows],
    }


def get_review(review_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM reviews WHERE review_id = ?", (review_id,)).fetchone()
    return _decode_review(row) if row else None


def product_stats(product_id: str) -> dict[str, Any]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT rating, COUNT(*) AS count FROM reviews WHERE product_id = ? GROUP BY rating",
            (product_id,),
        ).fetchall()
        summary = conn.execute(
            "SELECT COUNT(*) AS total, AVG(rating) AS avg_rating FROM reviews WHERE product_id = ?",
            (product_id,),
        ).fetchone()

    distribution = {str(i): 0 for i in range(1, 6)}
    for row in rows:
        distribution[str(row["rating"])] = row["count"]

    return {
        "product_id": product_id,
        "total": summary["total"] or 0,
        "avg_rating": round(float(summary["avg_rating"] or 0), 2),
        "distribution": distribution,
    }


def count_reviews() -> int:
    init_db()
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]


def _decode_review(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("images", "tokens", "topics"):
        try:
            item[key] = json.loads(item.get(key) or "[]")
        except json.JSONDecodeError:
            item[key] = []
    return item
