"""pxmemo — FastAPI web app for image timeline with EXIF metadata."""

import uuid
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
from PIL import Image

import db

THUMB_DIR = Path(__file__).parent / "thumbnails"
THUMB_MAX = 800  # reasonable display size


@asynccontextmanager
async def lifespan(app: FastAPI):
    THUMB_DIR.mkdir(exist_ok=True)
    db.init_db()
    yield


app = FastAPI(title="pxmemo", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.mount("/thumbnails", StaticFiles(directory=THUMB_DIR), name="thumbnails")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    images = db.get_all_images()
    return templates.TemplateResponse("timeline.html", {"request": request, "images": images})


@app.post("/upload")
async def upload_images(files: list[UploadFile] = File(...)):
    results = []
    for f in files:
        data = await f.read()
        if db.image_exists(f.filename):
            continue  # silently skip duplicates
        ext = Path(f.filename).suffix.lower()
        stored_name = f"{uuid.uuid4().hex[:12]}{ext}"

        thumb_path = THUMB_DIR / stored_name
        # Save and create display-size thumbnail
        temp_path = THUMB_DIR / f"_temp_{stored_name}"
        temp_path.write_bytes(data)
        try:
            img = Image.open(temp_path)
            img.thumbnail((THUMB_MAX, THUMB_MAX))
            img.save(thumb_path)
            temp_path.unlink()
        except Exception as e:
            temp_path.unlink(missing_ok=True)
            results.append({"filename": f.filename, "status": "error", "detail": str(e)})
            continue

        image_id = db.add_image(stored_name, f.filename)
        results.append({"filename": f.filename, "id": image_id, "status": "ok"})

    # Return the updated timeline
    return JSONResponse({"status": "ok", "uploaded": len([r for r in results if r["status"] == "ok"])})


@app.post("/reorder")
async def reorder(request: Request):
    data = await request.json()
    id_order = data.get("order", [])
    db.update_sort_order(id_order)
    return JSONResponse({"status": "ok"})


@app.post("/anchor/{image_id}")
async def set_anchor(image_id: int, request: Request):
    data = await request.json()
    date_str = data.get("date")
    if date_str:
        db.set_anchor_date(image_id, date_str)
    else:
        db.clear_anchor_date(image_id)
    return JSONResponse({"status": "ok"})


@app.delete("/image/{image_id}")
async def delete_image(image_id: int):
    image = db.get_image(image_id)
    if image:
        thumb = THUMB_DIR / image["filename"]
        thumb.unlink(missing_ok=True)
        db.delete_image(image_id)
    return JSONResponse({"status": "ok"})


@app.get("/timeline-data")
async def timeline_data():
    return db.get_all_images()
