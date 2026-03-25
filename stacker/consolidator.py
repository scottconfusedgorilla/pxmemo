"""PXStacker consolidator — produces one clean folder from a deduplicated archive.

Core principle: No Picture Left Behind. Every source image is accounted for
in the output. Originals are never touched.
"""

import csv
import re
import shutil
from pathlib import Path

import pyexiv2

import db

# Front/back filename patterns: (front_suffix, back_suffix)
PAIR_PATTERNS = [
    ("_a", "_b"),
    ("_front", "_back"),
    ("_f", "_b"),
    ("_1", "_2"),
]

PXLEGACY_NS = "http://pxlegacy.com/ns/1.0/"


def _copy_if_needed(src: str | Path, dest: str | Path) -> bool:
    """Copy file only if dest doesn't exist or differs in size. Returns True if copied."""
    src, dest = Path(src), Path(dest)
    if dest.exists() and dest.stat().st_size == src.stat().st_size:
        return False
    shutil.copy2(src, dest)
    return True


def detect_pairs(images: list[dict]) -> dict[int, int]:
    """Detect front/back photo pairs by filename convention.

    Returns dict mapping back_image_id → front_image_id.
    """
    # Build lookup by (directory, base_name_without_suffix)
    by_dir: dict[str, list[dict]] = {}
    for img in images:
        p = Path(img["file_path"])
        dir_key = str(p.parent)
        if dir_key not in by_dir:
            by_dir[dir_key] = []
        by_dir[dir_key].append(img)

    back_to_front: dict[int, int] = {}

    for dir_key, dir_images in by_dir.items():
        # Try each pattern
        for front_sfx, back_sfx in PAIR_PATTERNS:
            # Build index of potential fronts
            front_index: dict[str, dict] = {}
            for img in dir_images:
                stem = Path(img["filename"]).stem
                if stem.endswith(front_sfx):
                    base = stem[: -len(front_sfx)]
                    front_index[base] = img

            # Find matching backs
            for img in dir_images:
                if img["id"] in back_to_front:
                    continue
                stem = Path(img["filename"]).stem
                if stem.endswith(back_sfx):
                    base = stem[: -len(back_sfx)]
                    if base in front_index:
                        front = front_index[base]
                        # Validate: scan timestamps should be close (within 60 seconds)
                        if _timestamps_close(front, img, max_seconds=60):
                            back_to_front[img["id"]] = front["id"]

    return back_to_front


def _timestamps_close(img_a: dict, img_b: dict, max_seconds: int = 60) -> bool:
    """Check if two images have EXIF dates within max_seconds of each other."""
    from datetime import datetime

    date_a = img_a.get("exif_date")
    date_b = img_b.get("exif_date")
    if not date_a or not date_b:
        # No timestamps to validate — allow the pair (filename match is enough)
        return True
    try:
        dt_a = datetime.fromisoformat(date_a)
        dt_b = datetime.fromisoformat(date_b)
        return abs((dt_a - dt_b).total_seconds()) <= max_seconds
    except (ValueError, TypeError):
        return True


def _safe_copy_name(dest_dir: Path, filename: str, src_path: str | Path | None = None) -> Path:
    """Get a safe destination path, handling filename collisions.

    If src_path is provided and the destination already has a file with the
    same name and size, returns that existing path (no rename needed).
    Only generates a collision name (___N) when a genuinely different file
    already occupies the natural name.
    """
    dest = dest_dir / filename
    if not dest.exists():
        return dest
    # If source provided, check if the existing file IS the same source
    if src_path is not None:
        src = Path(src_path)
        if src.exists() and dest.stat().st_size == src.stat().st_size:
            return dest  # Same file already there
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 2
    while True:
        new_name = f"{stem}___{counter}{suffix}"
        dest = dest_dir / new_name
        if not dest.exists():
            return dest
        # Also check if this collision name already has our file
        if src_path is not None:
            src = Path(src_path)
            if src.exists() and dest.stat().st_size == src.stat().st_size:
                return dest
        counter += 1


def _write_xmp_pair(front_path: str, back_path: str, front_filename: str, back_filename: str):
    """Write cross-linked XMP metadata on both front and back images."""
    try:
        # Register custom namespace
        pyexiv2.registerNs(PXLEGACY_NS, "pxlegacy")
    except Exception:
        pass  # Already registered

    try:
        with pyexiv2.Image(front_path) as img:
            img.modify_xmp({
                "Xmp.xmpMM.DerivedFrom": back_filename,
                "Xmp.pxlegacy.LinkedTo": back_filename,
                "Xmp.pxlegacy.Side": "front",
            })
    except Exception as e:
        print(f"  Warning: could not write XMP to {front_path}: {e}")

    try:
        with pyexiv2.Image(back_path) as img:
            img.modify_xmp({
                "Xmp.xmpMM.DerivedFrom": front_filename,
                "Xmp.pxlegacy.LinkedTo": front_filename,
                "Xmp.pxlegacy.Side": "back",
            })
    except Exception as e:
        print(f"  Warning: could not write XMP to {back_path}: {e}")


def consolidate(output_folder: str, progress_callback=None) -> dict:
    """Consolidate all scanned images into one clean folder.

    Three-phase sync:
      Phase 1 (Plan)      — decide where every file should go, in memory only
      Phase 2 (Reconcile) — move/copy files to match the plan, reusing what's already there
      Phase 3 (Cleanup)   — delete anything in the output not in the plan

    Args:
        output_folder: Path to the output directory (will be created)
        progress_callback: Optional callable(processed, total, current_file)

    Returns:
        dict with stats and manifest data
    """
    out = Path(output_folder)
    out.mkdir(parents=True, exist_ok=True)

    # --- Load data ---
    all_images = db.get_all_images()
    all_stacks = db.get_all_stacks_with_members()
    stacked_ids = db.get_stacked_image_ids()

    back_to_front = detect_pairs(all_images)
    front_to_back = {v: k for k, v in back_to_front.items()}
    back_ids = set(back_to_front.keys())
    image_by_id = {img["id"]: img for img in all_images}

    # ===================================================================
    # PHASE 1: PLAN — build desired state without touching the filesystem
    # ===================================================================
    plan: dict[Path, str] = {}        # dest_path -> source_path
    manifest: list[dict] = []         # CSV rows
    xmp_pairs: list[tuple] = []       # (front_dest, back_dest, front_name, back_name)
    handled: set[int] = set()

    # In-memory collision detection (replaces filesystem-based _safe_copy_name)
    claimed: dict[str, str] = {}  # lowercased dest path -> source path

    def plan_dest(dest_dir: Path, filename: str, src_path: str) -> Path:
        """Claim a destination path, resolving collisions against the plan."""
        candidate = dest_dir / filename
        key = str(candidate).lower()
        if key not in claimed or claimed[key] == src_path:
            claimed[key] = src_path
            return candidate
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        counter = 2
        while True:
            candidate = dest_dir / f"{stem}___{counter}{suffix}"
            key = str(candidate).lower()
            if key not in claimed or claimed[key] == src_path:
                claimed[key] = src_path
                return candidate
            counter += 1

    def add_to_plan(dest: Path, src_path: str, filename: str, action: str, reason: str):
        plan[dest] = src_path
        manifest.append({
            "source_path": src_path,
            "filename": filename,
            "action": action,
            "destination": str(dest),
            "reason": reason,
        })

    # --- Plan stacks ---
    for stack in all_stacks:
        members = stack["members"]
        if not members:
            continue

        members = [m for m in members if m["id"] not in handled]
        if len(members) < 2:
            continue

        winner = db.pick_winner(members, stack.get("keep_image_id"))
        if not Path(winner["file_path"]).exists():
            continue

        winner_suffix = Path(winner["filename"]).suffix

        # Winner to output root
        winner_dest = plan_dest(out, winner["filename"], winner["file_path"])
        winner_dest_name = winner_dest.name
        add_to_plan(winner_dest, winner["file_path"], winner["filename"],
                     "keeper", f"highest resolution in stack #{stack['id']}")

        # Winner copy in stack folder for comparison
        stack_dir_name = f"{winner_dest.stem}_PXStack"
        stack_dir = out / stack_dir_name
        winner_stack_copy = plan_dest(stack_dir, winner["filename"], winner["file_path"])
        plan[winner_stack_copy] = winner["file_path"]  # no manifest row for the comparison copy

        # Winner's back
        if winner["id"] in front_to_back:
            back_img = image_by_id.get(front_to_back[winner["id"]])
            if back_img and Path(back_img["file_path"]).exists():
                back_dest_name = f"{winner_dest.stem}_back{winner_suffix}"
                back_dest = plan_dest(out, back_dest_name, back_img["file_path"])
                add_to_plan(back_dest, back_img["file_path"], back_img["filename"],
                             "paired", f"back of {winner_dest_name}")
                xmp_pairs.append((winner_dest, back_dest, winner_dest_name, back_dest.name))
                handled.add(back_img["id"])

        handled.add(winner["id"])

        # Losers to stack folder
        for member in members:
            if member["id"] == winner["id"]:
                continue
            if not Path(member["file_path"]).exists():
                handled.add(member["id"])
                continue

            loser_dest = plan_dest(stack_dir, member["filename"], member["file_path"])
            add_to_plan(loser_dest, member["file_path"], member["filename"],
                         "stacked", f"duplicate of {winner_dest_name}")

            # Loser's back
            if member["id"] in front_to_back:
                back_img = image_by_id.get(front_to_back[member["id"]])
                if back_img and Path(back_img["file_path"]).exists():
                    back_name = f"{loser_dest.stem}_back{Path(member['filename']).suffix}"
                    back_dest = plan_dest(stack_dir, back_name, back_img["file_path"])
                    add_to_plan(back_dest, back_img["file_path"], back_img["filename"],
                                 "paired", f"back of {loser_dest.name} (stacked)")
                    handled.add(back_img["id"])

            handled.add(member["id"])

    # --- Plan unique images ---
    for img in all_images:
        if img["id"] in handled:
            continue
        if img["id"] in back_ids:
            continue
        if img["id"] in stacked_ids:
            continue
        if not Path(img["file_path"]).exists():
            handled.add(img["id"])
            continue

        dest = plan_dest(out, img["filename"], img["file_path"])
        dest_name = dest.name
        add_to_plan(dest, img["file_path"], img["filename"], "copied", "unique")

        # Back
        if img["id"] in front_to_back:
            back_img = image_by_id.get(front_to_back[img["id"]])
            if back_img and Path(back_img["file_path"]).exists():
                back_dest_name = f"{dest.stem}_back{Path(img['filename']).suffix}"
                back_dest = plan_dest(out, back_dest_name, back_img["file_path"])
                add_to_plan(back_dest, back_img["file_path"], back_img["filename"],
                             "paired", f"back of {dest_name}")
                xmp_pairs.append((dest, back_dest, dest_name, back_dest.name))
                handled.add(back_img["id"])

        handled.add(img["id"])

    # --- Plan orphaned backs ---
    for img in all_images:
        if img["id"] in handled:
            continue
        if not Path(img["file_path"]).exists():
            handled.add(img["id"])
            continue

        dest = plan_dest(out, img["filename"], img["file_path"])
        add_to_plan(dest, img["file_path"], img["filename"],
                     "copied", "orphaned back (no front found)")
        handled.add(img["id"])

    # ===================================================================
    # PHASE 2: RECONCILE — make the output match the plan
    # ===================================================================

    # Index existing output files
    existing: dict[Path, int] = {}  # path -> file_size
    existing_by_name_size: dict[tuple, list[Path]] = {}  # (name_lower, size) -> [paths]
    for f in out.rglob("*"):
        if f.is_file() and f.name != "_manifest.csv":
            size = f.stat().st_size
            existing[f] = size
            key = (f.name.lower(), size)
            existing_by_name_size.setdefault(key, []).append(f)

    keep: set[Path] = set()  # paths that belong in the output
    total_actions = len(plan)
    processed = 0
    stats_skipped = 0
    stats_moved = 0
    stats_copied = 0

    plan_dest_set = set(plan.keys())  # for checking if a candidate is needed elsewhere

    for dest_path, src_path in plan.items():
        src = Path(src_path)
        src_size = src.stat().st_size
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        if dest_path in existing and existing[dest_path] == src_size:
            # Already correct
            stats_skipped += 1
        else:
            # Try to find a misplaced copy we can move
            moved = False
            lookup_key = (src.name.lower(), src_size)
            for candidate in existing_by_name_size.get(lookup_key, []):
                if candidate not in keep and candidate not in plan_dest_set:
                    shutil.move(str(candidate), str(dest_path))
                    del existing[candidate]
                    existing_by_name_size[lookup_key].remove(candidate)
                    stats_moved += 1
                    moved = True
                    break
            if not moved:
                shutil.copy2(src_path, str(dest_path))
                stats_copied += 1

        keep.add(dest_path)
        processed += 1
        if progress_callback:
            progress_callback(processed, total_actions, dest_path.name)

    # Write XMP pairs now that files are in final locations
    for front_dest, back_dest, front_name, back_name in xmp_pairs:
        _write_xmp_pair(str(front_dest), str(back_dest), front_name, back_name)

    # ===================================================================
    # PHASE 3: CLEANUP — delete anything not in the plan
    # ===================================================================
    stats_deleted = 0
    for f in list(out.rglob("*")):
        if f.is_file() and f not in keep and f.name != "_manifest.csv":
            f.unlink()
            stats_deleted += 1

    # Remove empty directories (bottom-up)
    for d in sorted([x for x in out.rglob("*") if x.is_dir()], reverse=True):
        try:
            d.rmdir()  # only succeeds if empty
        except OSError:
            pass

    # --- Write manifest ---
    manifest_path = out / "_manifest.csv"
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "source_path", "filename", "action", "destination", "reason",
        ])
        writer.writeheader()
        writer.writerows(manifest)

    # --- Stats ---
    stats = {
        "total_source": len(all_images),
        "keepers": sum(1 for m in manifest if m["action"] == "keeper"),
        "stacked": sum(1 for m in manifest if m["action"] == "stacked"),
        "copied": sum(1 for m in manifest if m["action"] == "copied"),
        "paired": sum(1 for m in manifest if m["action"] == "paired"),
        "manifest_rows": len(manifest),
        "manifest_path": str(manifest_path),
        "stacks_processed": len(all_stacks),
        "sync_skipped": stats_skipped,
        "sync_moved": stats_moved,
        "sync_copied": stats_copied,
        "sync_deleted": stats_deleted,
    }
    return stats
