"""PXStacker grouping logic — finds duplicates and resolution variants."""

from collections import defaultdict
import imagehash

import db

# pHash Hamming distance threshold: 0 = exact, <=4 = near-duplicate
EXACT_THRESHOLD = 0
NEAR_THRESHOLD = 4


def find_duplicate_stacks(progress_callback=None) -> dict:
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

    groups_with_dupes = [(ph, grp) for ph, grp in hash_groups.items()
                         if len([i for i in grp if i["id"] not in existing]) >= 2]
    total = len(groups_with_dupes)
    processed = 0

    for phash, group in groups_with_dupes:
        unstacked = [img for img in group if img["id"] not in existing]
        if len(unstacked) < 2:
            continue

        resolutions = {(img["width"], img["height"]) for img in unstacked}
        if len(resolutions) == 1:
            ids = [img["id"] for img in unstacked]
            db.create_stack("duplicate", label=f"Exact match: {unstacked[0]['filename']}", image_ids=ids)
            duplicate_stacks += 1
            existing.update(ids)
        else:
            ids = [img["id"] for img in unstacked]
            db.create_stack("lower_res", label=f"Resolution variants: {unstacked[0]['filename']}", image_ids=ids)
            lower_res_stacks += 1
            existing.update(ids)

        processed += 1
        if progress_callback:
            progress_callback("exact", processed, total)

    return {"duplicate_stacks": duplicate_stacks, "lower_res_stacks": lower_res_stacks}


def find_near_duplicates(progress_callback=None) -> dict:
    """Find images with similar (but not identical) pHashes.
    These are likely scans of the same photo with different settings.

    Uses union-find for transitive grouping: if A~B and B~C, all three
    end up in one stack even if A is not directly near C.

    Returns stats dict.
    """
    images = db.get_all_images()
    if not images:
        return {"near_duplicate_stacks": 0}

    existing = _get_stacked_image_ids()
    unstacked = [img for img in images if img["id"] not in existing and img["phash"]]

    total = len(unstacked)

    # Union-Find
    parent: dict[int, int] = {img["id"]: img["id"] for img in unstacked}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(a: int, b: int):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Compare all pairs, union near-matches
    hashes = {img["id"]: imagehash.hex_to_hash(img["phash"]) for img in unstacked}

    for i, img_a in enumerate(unstacked):
        ha = hashes[img_a["id"]]
        for img_b in unstacked[i + 1:]:
            hb = hashes[img_b["id"]]
            dist = ha - hb
            if 0 < dist <= NEAR_THRESHOLD:
                union(img_a["id"], img_b["id"])

        if progress_callback:
            progress_callback("near", i + 1, total)

    # Collect groups from union-find
    groups: dict[int, list[dict]] = defaultdict(list)
    for img in unstacked:
        root = find(img["id"])
        groups[root].append(img)

    stacks_created = 0
    for group in groups.values():
        if len(group) < 2:
            continue
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
