"""PXStacker grouping logic — finds duplicates and resolution variants."""

from collections import defaultdict
import imagehash
import numpy as np

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

    Uses vectorized numpy for fast Hamming distance computation, then
    union-find for transitive grouping: if A~B and B~C, all three
    end up in one stack even if A is not directly near C.

    Returns stats dict.
    """
    images = db.get_all_images()
    if not images:
        return {"near_duplicate_stacks": 0}

    existing = _get_stacked_image_ids()
    unstacked = [img for img in images if img["id"] not in existing and img["phash"]]

    total = len(unstacked)
    if total < 2:
        return {"near_duplicate_stacks": 0}

    # Convert hex pHash strings to uint64 array for vectorized comparison
    ids = [img["id"] for img in unstacked]
    hash_ints = np.array([int(img["phash"], 16) for img in unstacked], dtype=np.uint64)

    # Union-Find (array-based for speed)
    parent = np.arange(total, dtype=np.int32)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Byte-level popcount lookup table (0-255 -> bit count)
    _popcount_lut = np.zeros(256, dtype=np.uint8)
    for _i in range(256):
        _popcount_lut[_i] = bin(_i).count('1')

    def hamming_one_vs_many(h: np.uint64, candidates: np.ndarray) -> np.ndarray:
        """Compute Hamming distance between one hash and an array of hashes."""
        xor = np.bitwise_xor(h, candidates)
        # View as bytes, lookup popcount per byte, sum across 8 bytes
        xor_bytes = xor.view(np.uint8).reshape(-1, 8)
        return _popcount_lut[xor_bytes].sum(axis=1)

    # Compare each image against all later images, vectorized
    REPORT_INTERVAL = 500  # report progress every N images

    for i in range(total - 1):
        candidates = hash_ints[i + 1:]
        dists = hamming_one_vs_many(hash_ints[i], candidates)

        matches = np.where((dists > 0) & (dists <= NEAR_THRESHOLD))[0]
        for m in matches:
            union(i, i + 1 + m)

        if progress_callback and (i % REPORT_INTERVAL == 0 or i == total - 2):
            progress_callback("near", i + 1, total)

    # Collect groups from union-find
    groups: dict[int, list[dict]] = defaultdict(list)
    for i, img in enumerate(unstacked):
        root = find(i)
        groups[root].append(img)

    stacks_created = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        group_ids = [img["id"] for img in group]
        db.create_stack("duplicate", label=f"Near match: {group[0]['filename']}", image_ids=group_ids)
        stacks_created += 1

    return {"near_duplicate_stacks": stacks_created}


def _get_stacked_image_ids() -> set[int]:
    """Get set of image IDs already in any stack."""
    conn = db.get_db()
    rows = conn.execute("SELECT DISTINCT image_id FROM stack_members").fetchall()
    conn.close()
    return {r["image_id"] for r in rows}
