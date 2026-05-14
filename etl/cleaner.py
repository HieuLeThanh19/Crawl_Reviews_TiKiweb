"""
etl/cleaner.py
Làm sạch văn bản tiếng Việt từ đánh giá Tiki.

Các bước:
  1. Loại bỏ thẻ HTML
  2. Chuẩn hóa Unicode (NFC)
  3. Loại bỏ ký tự đặc biệt, emoji
  4. Chuẩn hóa khoảng trắng
  5. Chuẩn hóa dấu câu tiếng Việt
"""
import html
import re
import unicodedata
from typing import Optional


# Regex patterns — biên dịch trước để tái sử dụng
_RE_HTML_TAGS   = re.compile(r"<[^>]+>")
_RE_URL         = re.compile(r"https?://\S+|www\.\S+")
_RE_EMAIL       = re.compile(r"\S+@\S+\.\S+")
_RE_PHONE       = re.compile(r"(?:\+84|0)\d{9,10}")
_RE_EMOJI       = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)
_RE_SPECIAL     = re.compile(r"[^\w\s,.!?;:\-–()/\"'àáảãạăắặẳẵằâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđÀÁẢÃẠĂẮẶẲẴẰÂẤẦẨẪẬÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴĐ]", re.UNICODE)
_RE_WHITESPACE  = re.compile(r"\s+")
_RE_REPEAT_CHAR = re.compile(r"(.)\1{3,}")  # ký tự lặp 4+ lần: "đẹpppp" -> "đẹpp"


def clean_text(text: Optional[str]) -> str:
    """
    Pipeline làm sạch văn bản tiếng Việt.
    
    Args:
        text: Văn bản thô cần làm sạch
    
    Returns:
        Văn bản đã làm sạch
    
    Ví dụ:
        >>> clean_text("<p>Sản phẩm <b>tốt</b> lắm!!!</p>")
        'Sản phẩm tốt lắm!'
    """
    if not text or not isinstance(text, str):
        return ""

    # 1. Giải mã HTML entities (&amp; &lt; v.v.)
    text = html.unescape(text)

    # 2. Loại bỏ thẻ HTML
    text = _RE_HTML_TAGS.sub(" ", text)

    # 3. Chuẩn hóa Unicode về dạng NFC (quan trọng với tiếng Việt)
    text = unicodedata.normalize("NFC", text)

    # 4. Loại bỏ URL, email, số điện thoại (không cần thiết cho phân tích)
    text = _RE_URL.sub(" ", text)
    text = _RE_EMAIL.sub(" ", text)
    text = _RE_PHONE.sub(" ", text)

    # 5. Loại bỏ emoji
    text = _RE_EMOJI.sub(" ", text)

    # 6. Chuẩn hóa ký tự lặp: "haaaaa" → "haa"
    text = _RE_REPEAT_CHAR.sub(r"\1\1", text)

    # 7. Loại bỏ ký tự đặc biệt không phải tiếng Việt / dấu câu cơ bản
    text = _RE_SPECIAL.sub(" ", text)

    # 8. Chuẩn hóa khoảng trắng
    text = _RE_WHITESPACE.sub(" ", text).strip()

    # 9. Viết thường
    text = text.lower()

    return text


def clean_review(review: dict) -> dict:
    """
    Làm sạch các trường văn bản trong một đánh giá.
    Trả về bản sao đã làm sạch (không sửa đổi bản gốc).
    """
    cleaned = review.copy()
    cleaned["title_clean"]   = clean_text(review.get("title", ""))
    cleaned["content_clean"] = clean_text(review.get("content", ""))
    # Giữ nguyên content gốc để tham chiếu
    cleaned["content_raw"]   = review.get("content", "")
    return cleaned


def normalize_rating(rating: any) -> int:
    """Chuẩn hóa điểm sao về khoảng [1, 5]."""
    try:
        r = int(rating)
        return max(1, min(5, r))
    except (TypeError, ValueError):
        return 0  # không xác định
