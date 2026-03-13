"""pxmemo — drag-and-drop Windows tool that writes EXIF dates from pxdrop server."""

import sys
import ctypes
import logging
import tkinter as tk
from pathlib import Path

import piexif
import requests

SERVER = "http://localhost:8000"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

# Log file next to the executable (or script)
LOG_PATH = Path(sys.executable if getattr(sys, "frozen", False) else __file__).parent / "pxmemo.log"
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("pxmemo")


def lookup_memos(filenames: list[str]) -> dict:
    """Call pxdrop server to get metadata for the given filenames."""
    resp = requests.post(f"{SERVER}/lookup", json=filenames, timeout=10)
    resp.raise_for_status()
    return resp.json()


def date_to_exif(date_str: str) -> str:
    """Convert flexible date (YYYY, YYYY-MM, YYYY-MM-DD) to EXIF format.
    EXIF needs 'YYYY:MM:DD HH:MM:SS'.
    Year-only gets July 1, month-only gets the 15th."""
    date_str = date_str.strip()
    if len(date_str) == 4:  # YYYY
        return f"{date_str}:07:01 00:00:00"
    elif len(date_str) == 7:  # YYYY-MM
        parts = date_str.split("-")
        return f"{parts[0]}:{parts[1]}:15 00:00:00"
    elif len(date_str) == 10:  # YYYY-MM-DD
        return date_str.replace("-", ":") + " 00:00:00"
    else:
        return date_str.replace("-", ":") + " 00:00:00"


def read_existing_exif(filepath: Path) -> dict:
    """Read current EXIF date and description from an image."""
    result = {"date": None, "description": None}
    try:
        exif_dict = piexif.load(str(filepath))
        date_bytes = exif_dict.get("Exif", {}).get(piexif.ExifIFD.DateTimeOriginal)
        if date_bytes:
            result["date"] = date_bytes.decode("utf-8", errors="replace")
        desc_bytes = exif_dict.get("0th", {}).get(piexif.ImageIFD.ImageDescription)
        if desc_bytes:
            result["description"] = desc_bytes.decode("utf-8", errors="replace")
    except Exception:
        pass
    return result


def write_exif(filepath: Path, memo: dict) -> bool:
    """Write date and description from pxdrop into the image's EXIF data."""
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
        exif_date = date_to_exif(memo["date"])
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = exif_date.encode("utf-8")
        changed = True

    if changed:
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, str(filepath))

    return changed


def msgbox(title: str, text: str, style: int = 0):
    """Show a Windows message box (used for simple one-liners)."""
    ctypes.windll.user32.MessageBoxW(0, text, title, style)


class LogWindow:
    """Scrollable log window that shows processing results."""

    def __init__(self, title="pxmemo"):
        self.root = tk.Tk()
        self.root.title(title)
        self.root.configure(bg="#1a1a2e")
        self.root.geometry("700x500")

        self.text = tk.Text(
            self.root, wrap=tk.WORD, font=("Consolas", 10),
            bg="#1a1a2e", fg="#e0e0e0", insertbackground="#e0e0e0",
            selectbackground="#0f3460", relief=tk.FLAT, padx=10, pady=10,
        )
        scrollbar = tk.Scrollbar(self.root, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(fill=tk.BOTH, expand=True)

        # Tags for colored text
        self.text.tag_configure("header", foreground="#e94560", font=("Consolas", 11, "bold"))
        self.text.tag_configure("ok", foreground="#53a88b")
        self.text.tag_configure("skip", foreground="#888888")
        self.text.tag_configure("fail", foreground="#e94560")
        self.text.tag_configure("info", foreground="#e0e0e0")
        self.text.tag_configure("dim", foreground="#555555")

        self.text.configure(state=tk.DISABLED)

    def append(self, text: str, tag: str = "info"):
        self.text.configure(state=tk.NORMAL)
        self.text.insert(tk.END, text + "\n", tag)
        self.text.configure(state=tk.DISABLED)
        self.text.see(tk.END)
        self.root.update_idletasks()

    def show(self):
        self.root.mainloop()


def main():
    # When files are dropped onto the exe, they come as sys.argv[1:]
    files = sys.argv[1:]

    if not files:
        msgbox("pxmemo", "Drag and drop image files onto this icon to write EXIF dates from pxdrop.")
        return

    win = LogWindow()
    log.info("=" * 60)
    log.info("pxmemo run — %d file(s) dropped", len(files))
    win.append(f"pxmemo — {len(files)} file(s) dropped", "header")
    win.append("")

    # Filter to valid image paths
    paths = []
    for f in files:
        p = Path(f)
        if p.exists() and p.suffix.lower() in IMAGE_EXTS:
            paths.append(p)
        else:
            log.info("  IGNORE  %s (not a supported image)", f)
            win.append(f"  IGNORE  {Path(f).name}  (not a supported image)", "dim")

    if not paths:
        win.append("No valid image files found.", "fail")
        win.show()
        return

    # Lookup metadata from pxdrop
    filenames = [p.name for p in paths]
    log.info("Looking up %d file(s) on %s", len(filenames), SERVER)
    win.append(f"Looking up {len(filenames)} file(s) on {SERVER}...", "info")
    win.append("")
    try:
        memos = lookup_memos(filenames)
    except requests.ConnectionError:
        log.error("Cannot connect to %s", SERVER)
        win.append(f"Cannot connect to {SERVER}", "fail")
        win.append("Make sure the pxdrop server is running.", "fail")
        win.show()
        return
    except requests.RequestException as e:
        log.error("Server error: %s", e)
        win.append(f"Server error: {e}", "fail")
        win.show()
        return

    log.info("Server returned metadata for %d file(s)", len(memos))
    win.append(f"Server returned metadata for {len(memos)} file(s)", "info")
    win.append("")

    # Write EXIF data
    counts = {"ok": 0, "skip": 0, "fail": 0}
    for p in paths:
        memo = memos.get(p.name)
        if not memo:
            log.info("  SKIP  %s — not found on server", p.name)
            win.append(f"  SKIP  {p.name}  (not on server)", "skip")
            counts["skip"] += 1
            continue
        if not any(memo.get(k) for k in ("date", "description")):
            log.info("  SKIP  %s — no metadata to write", p.name)
            win.append(f"  SKIP  {p.name}  (no metadata)", "skip")
            counts["skip"] += 1
            continue

        # Read existing EXIF before writing
        before = read_existing_exif(p)
        new_date = date_to_exif(memo["date"]) if memo.get("date") else None

        log.info("  %s:", p.name)
        log.info("    EXIF before:  date=%s  desc=%s", before["date"] or "(none)", before["description"] or "(none)")
        log.info("    pxdrop says:  date=%s  desc=%s", memo.get("date", "(none)"), memo.get("description", "(none)"))

        win.append(f"  {p.name}", "info")
        win.append(f"    before:  {before['date'] or '(no date)'}",
                   "dim" if not before["date"] else "info")
        if new_date:
            log.info("    EXIF after:   date=%s", new_date)
            win.append(f"    after:   {new_date}", "ok")

        try:
            if write_exif(p, memo):
                log.info("    -> WRITTEN")
                win.append(f"    -> WRITTEN", "ok")
                counts["ok"] += 1
            else:
                log.info("    -> nothing changed")
                win.append(f"    -> nothing changed", "skip")
                counts["skip"] += 1
        except Exception as e:
            log.error("    -> FAILED: %s", e)
            win.append(f"    -> FAILED: {e}", "fail")
            counts["fail"] += 1

        win.append("")

    # Summary
    log.info("Summary: %d written, %d skipped, %d failed",
             counts["ok"], counts["skip"], counts["fail"])
    win.append("—" * 40, "dim")
    win.append(f"Done: {counts['ok']} written, {counts['skip']} skipped, {counts['fail']} failed", "header")

    win.show()


if __name__ == "__main__":
    main()
