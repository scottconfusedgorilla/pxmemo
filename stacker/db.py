"""PXStacker database layer — SQLite for local MVP."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "pxstacker.db"


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            phash TEXT,
            width INTEGER,
            height INTEGER,
            file_size INTEGER,
            exif_date TEXT,
            scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS stacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stack_type TEXT NOT NULL CHECK(stack_type IN ('duplicate', 'lower_res', 'manual')),
            label TEXT,
            resolved INTEGER NOT NULL DEFAULT 0,
            keep_image_id INTEGER REFERENCES images(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS stack_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stack_id INTEGER NOT NULL REFERENCES stacks(id) ON DELETE CASCADE,
            image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
            UNIQUE(stack_id, image_id)
        );

        CREATE INDEX IF NOT EXISTS idx_images_phash ON images(phash);
        CREATE INDEX IF NOT EXISTS idx_stack_members_image ON stack_members(image_id);
        CREATE INDEX IF NOT EXISTS idx_stack_members_stack ON stack_members(stack_id);
    """)
    conn.commit()
    conn.close()


# --- Images ---

def add_image(file_path: str, filename: str, phash: str, width: int, height: int,
              file_size: int, exif_date: str | None) -> int:
    conn = get_db()
    conn.execute(
        """INSERT OR IGNORE INTO images (file_path, filename, phash, width, height, file_size, exif_date)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (file_path, filename, phash, width, height, file_size, exif_date),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM images WHERE file_path = ?", (file_path,)).fetchone()
    conn.close()
    return row["id"]


def get_all_images() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM images ORDER BY filename").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_image(image_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_images_not_in_stacks() -> list[dict]:
    conn = get_db()
    rows = conn.execute("""
        SELECT i.* FROM images i
        WHERE i.id NOT IN (SELECT image_id FROM stack_members)
        ORDER BY i.filename
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_image_count() -> int:
    conn = get_db()
    row = conn.execute("SELECT COUNT(*) as cnt FROM images").fetchone()
    conn.close()
    return row["cnt"]


def search_images(query: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM images WHERE filename LIKE ? ORDER BY filename",
        (f"%{query}%",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Stacks ---

def create_stack(stack_type: str, label: str | None = None, image_ids: list[int] | None = None) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO stacks (stack_type, label) VALUES (?, ?)",
        (stack_type, label),
    )
    stack_id = cur.lastrowid
    if image_ids:
        for img_id in image_ids:
            conn.execute(
                "INSERT OR IGNORE INTO stack_members (stack_id, image_id) VALUES (?, ?)",
                (stack_id, img_id),
            )
    conn.commit()
    conn.close()
    return stack_id


def add_to_stack(stack_id: int, image_id: int):
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO stack_members (stack_id, image_id) VALUES (?, ?)",
        (stack_id, image_id),
    )
    conn.commit()
    conn.close()


def remove_from_stack(stack_id: int, image_id: int):
    conn = get_db()
    conn.execute(
        "DELETE FROM stack_members WHERE stack_id = ? AND image_id = ?",
        (stack_id, image_id),
    )
    # Delete stack if empty
    remaining = conn.execute(
        "SELECT COUNT(*) as cnt FROM stack_members WHERE stack_id = ?", (stack_id,)
    ).fetchone()["cnt"]
    if remaining == 0:
        conn.execute("DELETE FROM stacks WHERE id = ?", (stack_id,))
    conn.commit()
    conn.close()


def get_all_stacks(include_resolved: bool = False) -> list[dict]:
    conn = get_db()
    where = "" if include_resolved else "WHERE s.resolved = 0"
    rows = conn.execute(f"""
        SELECT s.*, COUNT(sm.image_id) as member_count
        FROM stacks s
        LEFT JOIN stack_members sm ON s.id = sm.stack_id
        {where}
        GROUP BY s.id
        ORDER BY s.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stack(stack_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM stacks WHERE id = ?", (stack_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_stack_members(stack_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute("""
        SELECT i.* FROM images i
        JOIN stack_members sm ON i.id = sm.image_id
        WHERE sm.stack_id = ?
        ORDER BY i.width * i.height DESC
    """, (stack_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resolve_stack(stack_id: int, keep_image_id: int | None = None):
    conn = get_db()
    conn.execute(
        "UPDATE stacks SET resolved = 1, keep_image_id = ? WHERE id = ?",
        (keep_image_id, stack_id),
    )
    conn.commit()
    conn.close()


def unresolve_stack(stack_id: int):
    conn = get_db()
    conn.execute(
        "UPDATE stacks SET resolved = 0, keep_image_id = NULL WHERE id = ?",
        (stack_id,),
    )
    conn.commit()
    conn.close()


def delete_stack(stack_id: int):
    conn = get_db()
    conn.execute("DELETE FROM stacks WHERE id = ?", (stack_id,))
    conn.commit()
    conn.close()


def get_stack_count(include_resolved: bool = False) -> dict:
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as cnt FROM stacks").fetchone()["cnt"]
    resolved = conn.execute("SELECT COUNT(*) as cnt FROM stacks WHERE resolved = 1").fetchone()["cnt"]
    conn.close()
    return {"total": total, "resolved": resolved, "pending": total - resolved}


# --- Export ---

def get_export_data() -> list[dict]:
    """Get resolved stacks with their members for CSV export."""
    conn = get_db()
    rows = conn.execute("""
        SELECT s.id as stack_id, s.stack_type, s.label,
               i.file_path, i.filename, i.width, i.height, i.file_size, i.phash,
               CASE WHEN s.keep_image_id = i.id THEN 1 ELSE 0 END as is_keeper
        FROM stacks s
        JOIN stack_members sm ON s.id = sm.stack_id
        JOIN images i ON sm.image_id = i.id
        WHERE s.resolved = 1
        ORDER BY s.id, is_keeper DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Reset ---

def reset_db():
    conn = get_db()
    conn.executescript("""
        DELETE FROM stack_members;
        DELETE FROM stacks;
        DELETE FROM images;
    """)
    conn.commit()
    conn.close()
