"""PXStacker grouping logic — finds duplicates and resolution variants."""

from collections import defaultdict
import imagehash

import db

# pHash Hamming distance threshold: 0 = exact, <=4 = near-duplicate
EXACT_THRESHOLD = 0
NEAR_THRESHOLD = 4


def find_duplicate_stacks() -> dict:
    """Group images by exact pHash match. Creates 'duplicate' stacks.
    Also detects resolution variants within each hash group.

    Returns stats dict.
    """
    images = db.get_all_images()
    if not images:
        return {"duplicate_stacks": 0, "lower_res_stacks": 0}

    # Group by exact phash
    hash_groups: dict[str, list[dict]] = defaultdict(list)
    for img in images:
        if img["phash"]:
            hash_groups[img["phash"]].append(img)

    duplicate_stacks = 0
    lower_res_stacks = 0

    # Get existing stack memberships to avoid re-stacking
    existing = _get_stacked_image_ids()

    for phash, group in hash_groups.items():
        # Filter out already-stacked images
        unstacked = [img for img in group if img["id"] not in existing]
        if len(unstacked) < 2:
            continue

        # Check if all same resolution → exact duplicates
        resolutions = {(img["width"], img["height"]) for img in unstacked}
        if len(resolutions) == 1:
            # All same resolution — exact duplicates
            ids = [img["id"] for img in unstacked]
            db.create_stack("duplicate", label=f"Exact match: {unstacked[0]['filename']}", image_ids=ids)
            duplicate_stacks += 1
            existing.update(ids)
        else:
            # Different resolutions — resolution variants
            ids = [img["id"] for img in unstacked]
            db.create_stack("lower_res", label=f"Resolution variants: {unstacked[0]['filename']}", image_ids=ids)
            lower_res_stacks += 1
            existing.update(ids)

    return {"duplicate_stacks": duplicate_stacks, "lower_res_stacks": lower_res_stacks}


def find_near_duplicates() -> dict:
    """Find images with similar (but not identical) pHashes.
    These are likely scans of the same photo with different settings.

    Returns stats dict.
    """
    images = db.get_all_images()
    if not images:
        return {"near_duplicate_stacks": 0}

    existing = _get_stacked_image_ids()
    unstacked = [img for img in images if img["id"] not in existing and img["phash"]]

    # Compare all pairs (O(n²) but fine for typical archive sizes)
    paired = set()
    groups = []

    for i, img_a in enumerate(unstacked):
        if img_a["id"] in paired:
            continue
        ha = imagehash.hex_to_hash(img_a["phash"])
        group = [img_a]

        for img_b in unstacked[i + 1:]:
            if img_b["id"] in paired:
                continue
            hb = imagehash.hex_to_hash(img_b["phash"])
            dist = ha - hb
            if 0 < dist <= NEAR_THRESHOLD:
                group.append(img_b)
                paired.add(img_b["id"])

        if len(group) >= 2:
            groups.append(group)
            paired.add(img_a["id"])

    stacks_created = 0
    for group in groups:
        ids = [img["id"] for img in group]
        db.create_stack("duplicate", label=f"Near match: {group[0]['filename']}", image_ids=ids)
        stacks_created += 1

    return {"near_duplicate_stacks": stacks_created}


def _get_stacked_image_ids() -> set[int]:
    """Get set of image IDs already in any stack."""
    conn = db.get_db()
    rows = conn.execute("SELECT DISTINCT image_id FROM stack_members").fetchall()
    conn.close()
    return {r["image_id"] for r in rows}
