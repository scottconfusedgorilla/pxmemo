"""PXStacker — local image deduplication tool for scanned photo archives."""

import csv
import io
import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
from PIL import Image, ImageOps

import db
import dater
import scanner
import stacker

THUMB_DIR = Path(__file__).parent / "thumbnails"
THUMB_MAX = 300

# Scan state (simple in-process tracking)
scan_state = {"running": False, "scanned": 0, "total": 0, "current_file": "", "result": None}
date_state = {"running": False, "processed": 0, "total": 0, "current_file": "", "result": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    THUMB_DIR.mkdir(exist_ok=True)
    db.init_db()
    yield


app = FastAPI(title="PXStacker", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.mount("/thumbnails", StaticFiles(directory=THUMB_DIR), name="thumbnails")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# --- Thumbnail helper ---

def ensure_thumbnail(file_path: str) -> str | None:
    """Create a thumbnail if it doesn't exist. Returns thumbnail filename."""
    src = Path(file_path)
    if not src.exists():
        return None
    thumb_name = f"{hash(file_path) & 0xFFFFFFFF:08x}{src.suffix.lower()}"
    thumb_path = THUMB_DIR / thumb_name
    if not thumb_path.exists():
        try:
            img = Image.open(src)
            img = ImageOps.exif_transpose(img)
            img.thumbnail((THUMB_MAX, THUMB_MAX))
            img.save(thumb_path)
            img.close()
        except Exception:
            return None
    return thumb_name


# --- Pages ---

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    counts = db.get_stack_count()
    image_count = db.get_image_count()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "counts": counts,
        "image_count": image_count,
        "scan_state": scan_state,
    })


@app.get("/stacks", response_class=HTMLResponse)
async def stacks_page(request: Request, show_resolved: str = ""):
    include_resolved = show_resolved == "on"
    stacks = db.get_all_stacks(include_resolved=include_resolved)
    return templates.TemplateResponse("stacks.html", {
        "request": request,
        "stacks": stacks,
        "show_resolved": include_resolved,
    })


@app.get("/stacks/{stack_id}", response_class=HTMLResponse)
async def stack_detail(request: Request, stack_id: int):
    stack = db.get_stack(stack_id)
    if not stack:
        return HTMLResponse("<p>Stack not found</p>", status_code=404)
    members = db.get_stack_members(stack_id)
    # Ensure thumbnails
    for m in members:
        m["thumb"] = ensure_thumbnail(m["file_path"])
    return templates.TemplateResponse("stack_detail.html", {
        "request": request,
        "stack": stack,
        "members": members,
    })


@app.get("/images", response_class=HTMLResponse)
async def images_page(request: Request, q: str = "", filter: str = "all"):
    if q:
        images = db.search_images(q)
    elif filter == "unstacked":
        images = db.get_images_not_in_stacks()
    else:
        images = db.get_all_images()
    for img in images:
        img["thumb"] = ensure_thumbnail(img["file_path"])
    return templates.TemplateResponse("images.html", {
        "request": request,
        "images": images,
        "query": q,
        "filter": filter,
    })


# --- Scan API ---

@app.post("/api/scan")
async def start_scan(request: Request):
    data = await request.json()
    folder = data.get("folder", "")
    if not folder or not Path(folder).is_dir():
        return JSONResponse({"error": "Invalid folder path"}, status_code=400)
    if scan_state["running"]:
        return JSONResponse({"error": "Scan already in progress"}, status_code=409)

    def run_scan():
        scan_state["running"] = True
        scan_state["scanned"] = 0
        scan_state["total"] = 0
        scan_state["result"] = None

        def progress(scanned, total, current_file):
            scan_state["scanned"] = scanned
            scan_state["total"] = total
            scan_state["current_file"] = Path(current_file).name

        try:
            result = scanner.scan_folder(folder, progress_callback=progress)
            scan_state["result"] = result
        except Exception as e:
            scan_state["result"] = {"error": str(e)}
        finally:
            scan_state["running"] = False

    thread = threading.Thread(target=run_scan, daemon=True)
    thread.start()
    return JSONResponse({"status": "started"})


@app.get("/api/scan/status")
async def scan_status():
    return JSONResponse(scan_state)


@app.post("/api/analyze")
async def analyze():
    """Run auto-detection: find duplicate and resolution variant stacks."""
    result_exact = stacker.find_duplicate_stacks()
    result_near = stacker.find_near_duplicates()
    return JSONResponse({
        "duplicate_stacks": result_exact["duplicate_stacks"],
        "lower_res_stacks": result_exact["lower_res_stacks"],
        "near_duplicate_stacks": result_near["near_duplicate_stacks"],
    })


# --- Date Estimation API ---

@app.post("/api/date-estimate")
async def start_date_estimate(request: Request):
    data = await request.json()
    sample_pct = float(data.get("sample_pct", 1.0))
    if date_state["running"]:
        return JSONResponse({"error": "Estimation already in progress"}, status_code=409)

    def run_estimate():
        date_state["running"] = True
        date_state["processed"] = 0
        date_state["total"] = 0
        date_state["result"] = None

        def progress(processed, total, current_file):
            date_state["processed"] = processed
            date_state["total"] = total
            date_state["current_file"] = current_file

        try:
            result = dater.estimate_batch(sample_pct, progress_callback=progress)
            date_state["result"] = result
        except Exception as e:
            date_state["result"] = {"error": str(e)}
        finally:
            date_state["running"] = False

    thread = threading.Thread(target=run_estimate, daemon=True)
    thread.start()
    return JSONResponse({"status": "started"})


@app.get("/api/date-estimate/status")
async def date_estimate_status():
    return JSONResponse(date_state)


@app.get("/date-results", response_class=HTMLResponse)
async def date_results_page(request: Request):
    result = date_state.get("result")
    return templates.TemplateResponse("date_results.html", {
        "request": request,
        "result": result,
        "date_state": date_state,
    })


# --- Stack API ---

@app.post("/api/stacks")
async def create_stack(request: Request):
    data = await request.json()
    stack_type = data.get("stack_type", "manual")
    label = data.get("label", "")
    image_ids = data.get("image_ids", [])
    if len(image_ids) < 2:
        return JSONResponse({"error": "Need at least 2 images"}, status_code=400)
    stack_id = db.create_stack(stack_type, label or None, image_ids)
    return JSONResponse({"status": "ok", "stack_id": stack_id})


@app.post("/api/stacks/{stack_id}/add")
async def add_to_stack(stack_id: int, request: Request):
    data = await request.json()
    image_id = data.get("image_id")
    if not image_id:
        return JSONResponse({"error": "image_id required"}, status_code=400)
    db.add_to_stack(stack_id, image_id)
    return JSONResponse({"status": "ok"})


@app.post("/api/stacks/{stack_id}/remove")
async def remove_from_stack(stack_id: int, request: Request):
    data = await request.json()
    image_id = data.get("image_id")
    if not image_id:
        return JSONResponse({"error": "image_id required"}, status_code=400)
    db.remove_from_stack(stack_id, image_id)
    return JSONResponse({"status": "ok"})


@app.post("/api/stacks/{stack_id}/resolve")
async def resolve_stack(stack_id: int, request: Request):
    data = await request.json()
    keep_id = data.get("keep_image_id")
    db.resolve_stack(stack_id, keep_id)
    return JSONResponse({"status": "ok"})


@app.post("/api/stacks/{stack_id}/unresolve")
async def unresolve_stack(stack_id: int):
    db.unresolve_stack(stack_id)
    return JSONResponse({"status": "ok"})


@app.delete("/api/stacks/{stack_id}")
async def delete_stack(stack_id: int):
    db.delete_stack(stack_id)
    return JSONResponse({"status": "ok"})


# --- Export ---

@app.get("/api/export")
async def export_csv():
    rows = db.get_export_data()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "stack_id", "stack_type", "label", "file_path", "filename",
        "width", "height", "file_size", "phash", "is_keeper",
    ])
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=pxstacker_report.csv"},
    )


# --- Reset ---

@app.post("/api/reset")
async def reset():
    db.reset_db()
    # Clean thumbnails
    for f in THUMB_DIR.iterdir():
        f.unlink(missing_ok=True)
    return JSONResponse({"status": "ok"})
