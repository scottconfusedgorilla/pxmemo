"""PXStacker scanner — recursive image scan with pHash and metadata extraction."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

from PIL import Image, ExifTags
import imagehash

import db

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp", ".gif"}
NUM_WORKERS = 4


def get_exif_date(img: Image.Image) -> str | None:
    """Extract the earliest date from EXIF data."""
    try:
        exif = img._getexif()
        if not exif:
            return None
        date_tags = [
            ExifTags.Base.DateTimeOriginal,
            ExifTags.Base.DateTimeDigitized,
            ExifTags.Base.DateTime,
        ]
        for tag in date_tags:
            val = exif.get(tag)
            if val:
                try:
                    dt = datetime.strptime(val, "%Y:%m:%d %H:%M:%S")
                    return dt.isoformat()
                except ValueError:
                    continue
    except Exception:
        pass
    return None


def compute_phash(img: Image.Image) -> str:
    """Compute perceptual hash for an image."""
    return str(imagehash.phash(img))


def _process_single_image(fpath_str: str) -> dict:
    """Process a single image file. Runs in a worker process."""
    fpath = Path(fpath_str)
    try:
        img = Image.open(fpath)
        phash = compute_phash(img)
        width, height = img.size
        file_size = fpath.stat().st_size
        exif_date = get_exif_date(img)
        img.close()
        return {
            "status": "ok",
            "file_path": fpath_str,
            "filename": fpath.name,
            "phash": phash,
            "width": width,
            "height": height,
            "file_size": file_size,
            "exif_date": exif_date,
        }
    except Exception as e:
        return {"status": "error", "file_path": fpath_str, "error": str(e)}


def scan_folder(folder_path: str, progress_callback=None) -> dict:
    """Recursively scan a folder for images, compute hashes and metadata.

    Uses multiprocessing for parallel hashing. Skips already-scanned files.

    Args:
        folder_path: Path to scan
        progress_callback: Optional callable(scanned, total, current_file)

    Returns:
        dict with scan statistics
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        raise ValueError(f"Not a valid directory: {folder_path}")

    # Collect all image files first
    image_files = []
    for root, _dirs, files in os.walk(folder):
        for fname in files:
            if Path(fname).suffix.lower() in IMAGE_EXTENSIONS:
                image_files.append(Path(root) / fname)

    # Load already-scanned paths from DB to skip
    existing = _get_scanned_paths()
    to_scan = [f for f in image_files if str(f) not in existing]
    skipped = len(image_files) - len(to_scan)

    total = len(to_scan)
    scanned = 0
    errors = 0

    if total == 0:
        if progress_callback:
            progress_callback(0, 0, "")
        return {"total_found": len(image_files), "scanned": 0, "skipped": skipped, "errors": 0}

    # Process in parallel (threads — GIL released during I/O and Pillow ops)
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {executor.submit(_process_single_image, str(f)): f for f in to_scan}

        for future in as_completed(futures):
            result = future.result()
            if result["status"] == "ok":
                db.add_image(
                    file_path=result["file_path"],
                    filename=result["filename"],
                    phash=result["phash"],
                    width=result["width"],
                    height=result["height"],
                    file_size=result["file_size"],
                    exif_date=result["exif_date"],
                )
                scanned += 1
            else:
                errors += 1
                print(f"  Error scanning {result['file_path']}: {result.get('error')}")

            if progress_callback:
                progress_callback(scanned + errors, total, result["file_path"])

    return {"total_found": len(image_files), "scanned": scanned, "skipped": skipped, "errors": errors}


def _get_scanned_paths() -> set[str]:
    """Get set of file paths already in the database."""
    conn = db.get_db()
    rows = conn.execute("SELECT file_path FROM images").fetchall()
    conn.close()
    return {r["file_path"] for r in rows}
