"""PXStacker date estimator — CLIP-based visual era detection."""

import random
from pathlib import Path

import torch
import open_clip
from PIL import Image

import db

# Decade prompts — CLIP scores each image against these
DECADE_PROMPTS = [
    ("1920s", "a photograph from the 1920s"),
    ("1930s", "a photograph from the 1930s"),
    ("1940s", "a photograph from the 1940s"),
    ("1950s", "a photograph from the 1950s"),
    ("1960s", "a photograph from the 1960s"),
    ("1970s", "a photograph from the 1970s"),
    ("1980s", "a photograph from the 1980s"),
    ("1990s", "a photograph from the 1990s"),
    ("2000s", "a photograph from the 2000s"),
    ("2010s", "a photograph from the 2010s"),
    ("2020s", "a photograph from the 2020s"),
]

# Module-level model cache
_model = None
_preprocess = None
_tokenizer = None
_text_features = None


def _load_model():
    """Load CLIP model (cached after first call)."""
    global _model, _preprocess, _tokenizer, _text_features

    if _model is not None:
        return

    model_name = "ViT-B-32"
    pretrained = "laion2b_s34b_b79k"

    _model, _, _preprocess = open_clip.create_model_and_transforms(
        model_name, pretrained=pretrained
    )
    _tokenizer = open_clip.get_tokenizer(model_name)
    _model.eval()

    # Pre-compute text embeddings for all decade prompts
    prompts = [p[1] for p in DECADE_PROMPTS]
    text_tokens = _tokenizer(prompts)
    with torch.no_grad():
        _text_features = _model.encode_text(text_tokens)
        _text_features /= _text_features.norm(dim=-1, keepdim=True)


def estimate_date(image_path: str) -> list[dict]:
    """Score a single image against all decade prompts.

    Returns list of {decade, score} sorted by score descending.
    """
    _load_model()

    img = Image.open(image_path).convert("RGB")
    img_tensor = _preprocess(img).unsqueeze(0)

    with torch.no_grad():
        image_features = _model.encode_image(img_tensor)
        image_features /= image_features.norm(dim=-1, keepdim=True)
        similarities = (image_features @ _text_features.T).squeeze(0)
        # Convert to probabilities
        probs = torch.softmax(similarities * 100, dim=0)

    results = []
    for i, (decade, _prompt) in enumerate(DECADE_PROMPTS):
        results.append({"decade": decade, "score": round(probs[i].item(), 4)})

    return sorted(results, key=lambda x: x["score"], reverse=True)


def estimate_batch(sample_pct: float = 1.0, progress_callback=None) -> dict:
    """Run date estimation on a random sample of scanned images.

    Args:
        sample_pct: Percentage of images to sample (0-100)
        progress_callback: Optional callable(processed, total, current_file)

    Returns:
        dict with results summary
    """
    _load_model()

    images = db.get_all_images()
    if not images:
        return {"sampled": 0, "results": []}

    # Sample N%
    sample_size = max(1, int(len(images) * sample_pct / 100))
    sample = random.sample(images, min(sample_size, len(images)))

    results = []
    processed = 0

    for img in sample:
        fpath = img["file_path"]
        if not Path(fpath).exists():
            continue

        try:
            scores = estimate_date(fpath)
            top = scores[0]
            result = {
                "image_id": img["id"],
                "filename": img["filename"],
                "file_path": fpath,
                "top_decade": top["decade"],
                "top_score": top["score"],
                "all_scores": scores,
            }
            results.append(result)
        except Exception as e:
            results.append({
                "image_id": img["id"],
                "filename": img["filename"],
                "file_path": fpath,
                "error": str(e),
            })

        processed += 1
        if progress_callback:
            progress_callback(processed, len(sample), img["filename"])

    return {
        "total_images": len(images),
        "sampled": len(sample),
        "sample_pct": sample_pct,
        "results": results,
    }
