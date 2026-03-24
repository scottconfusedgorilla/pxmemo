"""PXStacker scanner — recursive image scan with pHash and metadata extraction."""

import os
from pathlib import Path
from datetime import datetime

from PIL import Image, ExifTags
import imagehash

import db

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp", ".gif"}


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


def scan_folder(folder_path: str, progress_callback=None) -> dict:
    """Recursively scan a folder for images, compute hashes and metadata.

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

    total = len(image_files)
    scanned = 0
    skipped = 0
    errors = 0

    for fpath in image_files:
        try:
            img = Image.open(fpath)
            phash = compute_phash(img)
            width, height = img.size
            file_size = fpath.stat().st_size
            exif_date = get_exif_date(img)
            img.close()

            db.add_image(
                file_path=str(fpath),
                filename=fpath.name,
                phash=phash,
                width=width,
                height=height,
                file_size=file_size,
                exif_date=exif_date,
            )
            scanned += 1
        except Exception as e:
            errors += 1
            print(f"  Error scanning {fpath}: {e}")

        if progress_callback:
            progress_callback(scanned + errors + skipped, total, str(fpath))

    return {"total_found": total, "scanned": scanned, "skipped": skipped, "errors": errors}
