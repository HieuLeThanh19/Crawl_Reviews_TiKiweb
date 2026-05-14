#!/usr/bin/env python3
"""
scripts/backup_db.py
Script sao lưu và phục hồi cơ sở dữ liệu PostgreSQL.

Sử dụng:
    # Backup
    python scripts/backup_db.py backup

    # Restore từ file backup
    python scripts/backup_db.py restore --file backups/tiki_reviews_20260510_143000.sql

    # Liệt kê các file backup
    python scripts/backup_db.py list
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

BACKUP_DIR = Path("backups")


def backup(compress: bool = True) -> str:
    """
    Dump toàn bộ database ra file SQL (có nén gz).

    Returns:
        Đường dẫn file backup
    """
    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = ".sql.gz" if compress else ".sql"
    filename = BACKUP_DIR / f"tiki_reviews_{timestamp}{suffix}"

    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD

    cmd = [
        "pg_dump",
        "-h", DB_HOST,
        "-p", str(DB_PORT),
        "-U", DB_USER,
        "-d", DB_NAME,
        "--no-password",
        "--format=plain",
        "--encoding=UTF8",
    ]

    print(f"🔄 Đang backup database '{DB_NAME}' → {filename} ...")

    try:
        if compress:
            with open(filename, "wb") as f:
                p1 = subprocess.Popen(cmd, stdout=subprocess.PIPE, env=env)
                p2 = subprocess.Popen(["gzip"], stdin=p1.stdout, stdout=f)
                p1.stdout.close()
                p2.communicate()
            rc = p1.returncode
        else:
            with open(filename, "w", encoding="utf-8") as f:
                result = subprocess.run(cmd, stdout=f, env=env)
            rc = result.returncode

        if rc == 0:
            size = filename.stat().st_size / 1024 / 1024
            print(f"✅ Backup thành công: {filename} ({size:.1f} MB)")
            return str(filename)
        else:
            print(f"❌ pg_dump thất bại (exit code {rc})")
            return ""
    except FileNotFoundError:
        print("❌ pg_dump không tìm thấy. Cài PostgreSQL client: apt install postgresql-client")
        return ""


def restore(filepath: str) -> bool:
    """
    Restore database từ file backup.

    Args:
        filepath: Đường dẫn file .sql hoặc .sql.gz

    Returns:
        True nếu thành công
    """
    path = Path(filepath)
    if not path.exists():
        print(f"❌ File không tồn tại: {filepath}")
        return False

    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD

    print(f"⚠️  Chuẩn bị restore '{path.name}' vào database '{DB_NAME}'")
    confirm = input("Tiếp tục? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("Đã hủy.")
        return False

    cmd_psql = [
        "psql",
        "-h", DB_HOST,
        "-p", str(DB_PORT),
        "-U", DB_USER,
        "-d", DB_NAME,
        "--no-password",
    ]

    print(f"🔄 Đang restore ...")
    try:
        if filepath.endswith(".gz"):
            p1 = subprocess.Popen(["gunzip", "-c", filepath], stdout=subprocess.PIPE)
            p2 = subprocess.Popen(cmd_psql, stdin=p1.stdout, env=env)
            p1.stdout.close()
            p2.communicate()
            rc = p2.returncode
        else:
            with open(filepath, "r", encoding="utf-8") as f:
                result = subprocess.run(cmd_psql, stdin=f, env=env)
            rc = result.returncode

        if rc == 0:
            print("✅ Restore thành công!")
            return True
        else:
            print(f"❌ Restore thất bại (exit code {rc})")
            return False
    except FileNotFoundError as e:
        print(f"❌ Lỗi: {e}")
        return False


def list_backups():
    """Liệt kê tất cả file backup."""
    if not BACKUP_DIR.exists():
        print("Chưa có file backup nào.")
        return

    files = sorted(BACKUP_DIR.glob("*.sql*"), reverse=True)
    if not files:
        print("Chưa có file backup nào.")
        return

    print(f"\n📁 Các file backup trong '{BACKUP_DIR}':\n")
    print(f"{'Tên file':<45} {'Kích thước':>12} {'Ngày tạo'}")
    print("-" * 80)
    for f in files:
        stat = f.stat()
        size = stat.st_size / 1024 / 1024
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"{f.name:<45} {size:>10.1f}MB  {mtime}")

    print(f"\nTổng: {len(files)} files")


def cleanup_old_backups(keep_days: int = 30):
    """Xóa các backup cũ hơn keep_days ngày."""
    if not BACKUP_DIR.exists():
        return

    cutoff = datetime.now().timestamp() - keep_days * 86400
    removed = 0
    for f in BACKUP_DIR.glob("*.sql*"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            print(f"🗑️  Đã xóa backup cũ: {f.name}")
            removed += 1

    if removed == 0:
        print(f"Không có backup nào cũ hơn {keep_days} ngày.")
    else:
        print(f"✅ Đã xóa {removed} file backup cũ.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backup & Restore database Tiki Reviews")
    subs = parser.add_subparsers(dest="cmd", required=True)

    p_bk = subs.add_parser("backup", help="Tạo file backup")
    p_bk.add_argument("--no-compress", action="store_true", help="Không nén gzip")

    p_rs = subs.add_parser("restore", help="Restore từ file backup")
    p_rs.add_argument("--file", required=True, help="Đường dẫn file backup")

    subs.add_parser("list", help="Liệt kê các file backup")

    p_cl = subs.add_parser("cleanup", help="Xóa backup cũ")
    p_cl.add_argument("--keep-days", type=int, default=30, help="Giữ lại backup trong N ngày")

    args = parser.parse_args()

    if args.cmd == "backup":
        backup(compress=not args.no_compress)
    elif args.cmd == "restore":
        restore(args.file)
    elif args.cmd == "list":
        list_backups()
    elif args.cmd == "cleanup":
        cleanup_old_backups(args.keep_days)
