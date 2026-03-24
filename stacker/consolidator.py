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


def _safe_copy_name(dest_dir: Path, filename: str) -> Path:
    """Get a safe destination path, handling filename collisions."""
    dest = dest_dir / filename
    if not dest.exists():
        return dest
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 2
    while True:
        new_name = f"{stem}_{counter}{suffix}"
        dest = dest_dir / new_name
        if not dest.exists():
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

    Args:
        output_folder: Path to the output directory (will be created)
        progress_callback: Optional callable(processed, total, current_file)

    Returns:
        dict with stats and manifest data
    """
    out = Path(output_folder)
    out.mkdir(parents=True, exist_ok=True)

    # 1. Get all data
    all_images = db.get_all_images()
    all_stacks = db.get_all_stacks_with_members()
    stacked_ids = db.get_stacked_image_ids()

    # 2. Detect front/back pairs
    back_to_front = detect_pairs(all_images)
    front_to_back = {v: k for k, v in back_to_front.items()}
    back_ids = set(back_to_front.keys())

    # 3. Build image lookup
    image_by_id = {img["id"]: img for img in all_images}

    # 4. Plan all actions
    manifest = []  # list of dicts for CSV
    total_actions = len(all_images)
    processed = 0

    # Track which images have been handled
    handled = set()

    # --- Process stacks ---
    for stack in all_stacks:
        members = stack["members"]
        if not members:
            continue

        winner = db.pick_winner(members, stack.get("keep_image_id"))
        winner_stem = Path(winner["filename"]).stem
        winner_suffix = Path(winner["filename"]).suffix
        stack_dir_name = f"{winner_stem}_PXStack"

        # Copy winner to output
        winner_dest = _safe_copy_name(out, winner["filename"])
        winner_dest_name = winner_dest.name

        # Create stack subfolder
        stack_dir = out / stack_dir_name
        stack_dir.mkdir(exist_ok=True)

        # Copy winner to output root
        if Path(winner["file_path"]).exists():
            shutil.copy2(winner["file_path"], winner_dest)
            manifest.append({
                "source_path": winner["file_path"],
                "filename": winner["filename"],
                "action": "keeper",
                "destination": str(winner_dest),
                "reason": f"highest resolution in stack #{stack['id']}",
            })

            # Copy winner into stack folder for comparison
            shutil.copy2(winner["file_path"], stack_dir / winner["filename"])

            # Handle winner's back
            if winner["id"] in front_to_back:
                back_img = image_by_id.get(front_to_back[winner["id"]])
                if back_img and Path(back_img["file_path"]).exists():
                    back_dest_name = f"{winner_dest.stem}_back{winner_suffix}"
                    back_dest = out / back_dest_name
                    shutil.copy2(back_img["file_path"], back_dest)
                    _write_xmp_pair(str(winner_dest), str(back_dest),
                                    winner_dest_name, back_dest_name)
                    manifest.append({
                        "source_path": back_img["file_path"],
                        "filename": back_img["filename"],
                        "action": "paired",
                        "destination": str(back_dest),
                        "reason": f"back of {winner_dest_name}",
                    })
                    handled.add(back_img["id"])

        handled.add(winner["id"])
        processed += 1
        if progress_callback:
            progress_callback(processed, total_actions, winner["filename"])

        # Copy losers to stack folder
        for member in members:
            if member["id"] == winner["id"]:
                continue
            if Path(member["file_path"]).exists():
                loser_dest = _safe_copy_name(stack_dir, member["filename"])
                shutil.copy2(member["file_path"], loser_dest)
                manifest.append({
                    "source_path": member["file_path"],
                    "filename": member["filename"],
                    "action": "stacked",
                    "destination": str(loser_dest),
                    "reason": f"duplicate of {winner_dest_name}",
                })

                # Handle loser's back
                if member["id"] in front_to_back:
                    back_img = image_by_id.get(front_to_back[member["id"]])
                    if back_img and Path(back_img["file_path"]).exists():
                        back_name = f"{loser_dest.stem}_back{Path(member['filename']).suffix}"
                        back_dest = stack_dir / back_name
                        shutil.copy2(back_img["file_path"], back_dest)
                        manifest.append({
                            "source_path": back_img["file_path"],
                            "filename": back_img["filename"],
                            "action": "paired",
                            "destination": str(back_dest),
                            "reason": f"back of {loser_dest.name} (stacked)",
                        })
                        handled.add(back_img["id"])

            handled.add(member["id"])
            processed += 1
            if progress_callback:
                progress_callback(processed, total_actions, member["filename"])

    # --- Process unique images (not in any stack, not a back) ---
    for img in all_images:
        if img["id"] in handled:
            continue
        if img["id"] in back_ids:
            # This back's front wasn't in a stack — will be handled with its front
            continue
        if img["id"] in stacked_ids:
            continue

        if Path(img["file_path"]).exists():
            dest = _safe_copy_name(out, img["filename"])
            dest_name = dest.name
            shutil.copy2(img["file_path"], dest)
            manifest.append({
                "source_path": img["file_path"],
                "filename": img["filename"],
                "action": "copied",
                "destination": str(dest),
                "reason": "unique",
            })

            # Handle back
            if img["id"] in front_to_back:
                back_img = image_by_id.get(front_to_back[img["id"]])
                if back_img and Path(back_img["file_path"]).exists():
                    back_dest_name = f"{dest.stem}_back{Path(img['filename']).suffix}"
                    back_dest = out / back_dest_name
                    shutil.copy2(back_img["file_path"], back_dest)
                    _write_xmp_pair(str(dest), str(back_dest), dest_name, back_dest_name)
                    manifest.append({
                        "source_path": back_img["file_path"],
                        "filename": back_img["filename"],
                        "action": "paired",
                        "destination": str(back_dest),
                        "reason": f"back of {dest_name}",
                    })
                    handled.add(back_img["id"])

        handled.add(img["id"])
        processed += 1
        if progress_callback:
            progress_callback(processed, total_actions, img["filename"])

    # --- Handle any orphaned backs (front not found) ---
    for img in all_images:
        if img["id"] in handled:
            continue
        if Path(img["file_path"]).exists():
            dest = _safe_copy_name(out, img["filename"])
            shutil.copy2(img["file_path"], dest)
            manifest.append({
                "source_path": img["file_path"],
                "filename": img["filename"],
                "action": "copied",
                "destination": str(dest),
                "reason": "orphaned back (no front found)",
            })
        handled.add(img["id"])
        processed += 1
        if progress_callback:
            progress_callback(processed, total_actions, img["filename"])

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
    }
    return stats
