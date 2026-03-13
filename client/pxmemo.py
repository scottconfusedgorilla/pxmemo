"""pxmemo local client — writes EXIF data from cloud metadata into image files."""

import argparse
import sys
from pathlib import Path

import piexif
import requests


DEFAULT_SERVER = "http://localhost:8000"


def lookup_memos(server: str, filenames: list[str]) -> dict:
    """Call the cloud server to get metadata for the given filenames."""
    resp = requests.post(f"{server}/lookup", json=filenames)
    resp.raise_for_status()
    return resp.json()


def decimal_to_dms(decimal: float) -> tuple:
    """Convert decimal degrees to EXIF-format degrees/minutes/seconds."""
    d = int(abs(decimal))
    m = int((abs(decimal) - d) * 60)
    s = int(((abs(decimal) - d) * 60 - m) * 60 * 10000)
    return ((d, 1), (m, 1), (s, 10000))


def write_exif(filepath: Path, memo: dict) -> bool:
    """Write metadata from the cloud memo into the image's EXIF data."""
    try:
        exif_dict = piexif.load(str(filepath))
    except Exception:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}

    changed = False

    if memo.get("description"):
        desc = memo["description"].encode("utf-8")
        exif_dict["0th"][piexif.ImageIFD.ImageDescription] = desc
        changed = True

    if memo.get("date"):
        # EXIF date format: "YYYY:MM:DD HH:MM:SS"
        date_str = memo["date"].replace("-", ":")
        if len(date_str) == 10:
            date_str += " 00:00:00"
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = date_str.encode("utf-8")
        changed = True

    if memo.get("latitude") is not None and memo.get("longitude") is not None:
        lat = memo["latitude"]
        lon = memo["longitude"]
        exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = decimal_to_dms(lat)
        exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = b"N" if lat >= 0 else b"S"
        exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = decimal_to_dms(lon)
        exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = b"E" if lon >= 0 else b"W"
        changed = True

    if changed:
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, str(filepath))

    return changed


def main():
    parser = argparse.ArgumentParser(
        description="pxmemo — write cloud metadata into local image EXIF data"
    )
    parser.add_argument("images", nargs="+", help="Image files to process")
    parser.add_argument(
        "--server", default=DEFAULT_SERVER, help=f"pxmemo server URL (default: {DEFAULT_SERVER})"
    )
    args = parser.parse_args()

    # Collect valid image paths
    paths = []
    for img in args.images:
        p = Path(img)
        if not p.exists():
            print(f"  SKIP  {img} (file not found)")
            continue
        paths.append(p)

    if not paths:
        print("No valid image files provided.")
        sys.exit(1)

    # Lookup metadata from server
    filenames = [p.name for p in paths]
    print(f"Looking up {len(filenames)} file(s) on {args.server}...")
    try:
        memos = lookup_memos(args.server, filenames)
    except requests.RequestException as e:
        print(f"Error contacting server: {e}")
        sys.exit(1)

    # Write EXIF data
    for p in paths:
        memo = memos.get(p.name)
        if not memo:
            print(f"  SKIP  {p.name} (not found on server)")
            continue

        has_data = any(memo.get(k) for k in ("date", "description", "latitude"))
        if not has_data:
            print(f"  SKIP  {p.name} (no metadata to write)")
            continue

        try:
            if write_exif(p, memo):
                print(f"  OK    {p.name}")
            else:
                print(f"  SKIP  {p.name} (nothing to write)")
        except Exception as e:
            print(f"  FAIL  {p.name} ({e})")


if __name__ == "__main__":
    main()
