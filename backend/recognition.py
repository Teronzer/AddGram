import os
from pathlib import Path

_model = None
_model_path = Path(os.getenv("ADDGRAM_MODEL", Path(__file__).parent / "models" / "best.pt"))

def predict_foods(image_path: str, top_k: int = 3):
    """Return [{class_name, confidence}]. Empty list means no model is installed."""
    global _model
    if not _model_path.exists():
        return []
    try:
        from ultralytics import YOLO
        if _model is None:
            _model = YOLO(str(_model_path))
        results = _model.predict(source=image_path, verbose=False)
        probs = results[0].probs
        if probs is None:
            return []
        names = results[0].names
        indices = probs.top5[:top_k]
        return [
            {"class_name": str(names[int(i)]), "confidence": float(probs.data[int(i)].item())}
            for i in indices
        ]
    except Exception as exc:
        print(f"Food recognition error: {exc}")
        return []
