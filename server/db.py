"""pxmemo database layer — SQLite for POC."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "pxmemo.db"


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE NOT NULL,
            original_name TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            anchor_date TEXT,
            computed_date TEXT,
            description TEXT
        )
    """)
    conn.commit()
    conn.close()


def image_exists(original_name: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT 1 FROM images WHERE original_name = ?", (original_name,)).fetchone()
    conn.close()
    return row is not None


def add_image(filename: str, original_name: str) -> int:
    conn = get_db()
    # Put new images at the end
    max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) FROM images").fetchone()[0]
    conn.execute(
        "INSERT INTO images (filename, original_name, sort_order) VALUES (?, ?, ?)",
        (filename, original_name, max_order + 1),
    )
    conn.commit()
    image_id = conn.execute("SELECT id FROM images WHERE filename = ?", (filename,)).fetchone()[0]
    conn.close()
    return image_id


def get_all_images() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM images ORDER BY sort_order").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_image(image_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def date_precision(date_str: str) -> str:
    """Return 'year', 'month', or 'day' based on date string format."""
    date_str = date_str.strip()
    if len(date_str) == 4:
        return "year"
    elif len(date_str) == 7:
        return "month"
    return "day"


def parse_date_to_datetime(date_str: str) -> datetime:
    """Parse flexible date formats to datetime for sorting/interpolation.
    '2024' -> 2024-07-01, '2024-06' -> 2024-06-15, '2024-06-15' -> 2024-06-15
    """
    date_str = date_str.strip()
    if len(date_str) == 4:  # year only
        return datetime(int(date_str), 7, 1)
    elif len(date_str) == 7:  # year-month
        parts = date_str.split("-")
        return datetime(int(parts[0]), int(parts[1]), 15)
    else:
        return datetime.strptime(date_str, "%Y-%m-%d")


def format_interpolated_date(dt: datetime, precision: str) -> str:
    """Format an interpolated datetime to the appropriate precision.

    Precision rules:
      year:  round to nearest year  -> 'YYYY'
      month: round to nearest month -> 'YYYY-MM'
      day:   round to nearest day   -> 'YYYY-MM-DD'
    """
    if precision == "year":
        return str(dt.year)
    elif precision == "month":
        return f"{dt.year:04d}-{dt.month:02d}"
    else:
        return dt.strftime("%Y-%m-%d")


def is_precision_refinement(old_date: str, new_date: str) -> bool:
    """Check if new_date is just a more precise version of old_date.
    e.g. '1971' -> '1971-02' or '1971-02' -> '1971-02-15'."""
    if not old_date:
        return False
    return new_date.startswith(old_date) and len(new_date) > len(old_date)


def set_anchor_date(image_id: int, date_str: str, resort: bool = True):
    conn = get_db()
    old = conn.execute("SELECT anchor_date FROM images WHERE id = ?", (image_id,)).fetchone()
    old_date = old["anchor_date"] if old else None
    conn.execute(
        "UPDATE images SET anchor_date = ?, computed_date = ? WHERE id = ?",
        (date_str, date_str, image_id),
    )
    conn.commit()
    conn.close()
    if not resort or is_precision_refinement(old_date, date_str):
        recompute_dates()  # recalc neighbors but don't move this image
    else:
        resort_by_date()


def clear_anchor_date(image_id: int):
    conn = get_db()
    conn.execute(
        "UPDATE images SET anchor_date = NULL, computed_date = NULL WHERE id = ?",
        (image_id,),
    )
    conn.commit()
    conn.close()
    recompute_dates()


def resort_by_date():
    """Sort anchored images into chronological order while preserving the
    relative positions of unanchored images among their neighboring anchors.

    Algorithm: walk the current order, note which slots are anchors.
    Sort the anchors chronologically, then put them back into those same
    slots. Unanchored images stay exactly where they were."""
    conn = get_db()
    rows = conn.execute("SELECT id, anchor_date, sort_order FROM images ORDER BY sort_order").fetchall()

    # Collect anchor slot indices and sort the anchors chronologically
    anchor_slots = []  # (index_in_list, id, anchor_date)
    for i, r in enumerate(rows):
        if r["anchor_date"]:
            anchor_slots.append((i, r["id"], r["anchor_date"]))

    sorted_anchors = sorted(anchor_slots, key=lambda x: parse_date_to_datetime(x[2]))

    # Build new order: start with current order, then swap anchors into sorted positions
    new_order = [r["id"] for r in rows]
    slot_indices = [s[0] for s in anchor_slots]  # original slot positions
    for slot_idx, (_, anchor_id, _) in zip(slot_indices, sorted_anchors):
        new_order[slot_idx] = anchor_id

    for i, img_id in enumerate(new_order):
        conn.execute("UPDATE images SET sort_order = ? WHERE id = ?", (i, img_id))

    conn.commit()
    conn.close()
    recompute_dates()


def update_sort_order(id_order: list[int]):
    """Update sort_order based on a list of image IDs in desired order."""
    conn = get_db()
    for i, image_id in enumerate(id_order):
        conn.execute("UPDATE images SET sort_order = ? WHERE id = ?", (i, image_id))
    conn.commit()
    conn.close()
    recompute_dates()


def recompute_dates():
    """Linearly interpolate dates for non-anchor images between anchors."""
    conn = get_db()
    images = conn.execute("SELECT id, sort_order, anchor_date FROM images ORDER BY sort_order").fetchall()

    if not images:
        conn.close()
        return

    # Build list: [(index_in_list, id, anchor_date_or_None), ...]
    items = [(i, row["id"], row["anchor_date"]) for i, row in enumerate(images)]

    # Find anchor positions
    anchors = [(i, dt) for i, _id, dt in items if dt is not None]

    if len(anchors) == 0:
        # No anchors — clear all computed dates
        conn.execute("UPDATE images SET computed_date = NULL WHERE anchor_date IS NULL")
        conn.commit()
        conn.close()
        return

    # Interpolate between each pair of anchors
    for idx in range(len(items)):
        i, img_id, anchor = items[idx]
        if anchor is not None:
            continue  # anchors keep their own date

        # Find nearest anchor before and after
        prev_anchor = None
        next_anchor = None
        for ai, adt in anchors:
            if ai < idx:
                prev_anchor = (ai, adt)
            elif ai > idx and next_anchor is None:
                next_anchor = (ai, adt)

        if prev_anchor and next_anchor:
            # Interpolate between two anchors
            d1 = parse_date_to_datetime(prev_anchor[1])
            d2 = parse_date_to_datetime(next_anchor[1])
            total_slots = next_anchor[0] - prev_anchor[0]
            position = idx - prev_anchor[0]
            delta = (d2 - d1) * position / total_slots
            computed = d1 + delta
            # Use the coarser precision of the two bounding anchors
            p1 = date_precision(prev_anchor[1])
            p2 = date_precision(next_anchor[1])
            prec_order = {"year": 0, "month": 1, "day": 2}
            precision = p1 if prec_order[p1] <= prec_order[p2] else p2
            formatted = format_interpolated_date(computed, precision)
            conn.execute(
                "UPDATE images SET computed_date = ? WHERE id = ?",
                (formatted, img_id),
            )
        elif prev_anchor:
            # After last anchor — no interpolation possible
            conn.execute("UPDATE images SET computed_date = NULL WHERE id = ?", (img_id,))
        elif next_anchor:
            # Before first anchor — no interpolation possible
            conn.execute("UPDATE images SET computed_date = NULL WHERE id = ?", (img_id,))

    conn.commit()
    conn.close()


def get_image_by_name(original_name: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM images WHERE original_name = ?", (original_name,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_image(image_id: int):
    conn = get_db()
    conn.execute("DELETE FROM images WHERE id = ?", (image_id,))
    conn.commit()
    conn.close()
    recompute_dates()


def delete_all_images():
    conn = get_db()
    conn.execute("DELETE FROM images")
    conn.commit()
    conn.close()
