from pathlib import Path
import shutil
import tempfile
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import init_db, connect
from .recognition import predict_foods

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="AddGram API", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")

@app.on_event("startup")
def startup():
    init_db()

class AddMealRequest(BaseModel):
    class_name: str
    weight_g: float

class SelectFoodRequest(BaseModel):
    class_name: str

def get_food(class_name):
    with connect() as c:
        row = c.execute("SELECT * FROM foods WHERE class_name=?", (class_name,)).fetchone()
        return dict(row) if row else None

def nutrition_for(food, weight_g):
    factor = float(weight_g) / 100.0
    return {
        "kcal": round(food["kcal"] * factor, 2),
        "protein_g": round(food["protein_g"] * factor, 2),
        "carbs_g": round(food["carbs_g"] * factor, 2),
        "fat_g": round(food["fat_g"] * factor, 2),
    }

@app.get("/", response_class=HTMLResponse)
def home():
    return (ROOT / "web" / "index.html").read_text(encoding="utf-8")

@app.get("/health")
def health():
    return {"status": "ok"}
class WeightUpdate(BaseModel):
    weight_g: float


# The Pi posts every ~1 s. If nothing arrived for this long, the scale
# is shown as disconnected instead of freezing on the last value.
LIVE_WEIGHT_STALE_S = 5.0


@app.post("/weight/update")
def update_weight(req: WeightUpdate):
    import json
    import time

    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO app_state(key, value) VALUES('live_weight', ?)",
            (json.dumps({"weight_g": req.weight_g, "ts": time.time()}),)
        )
    return {"received": True}


@app.get("/weight")
def get_weight():
    import json
    import time

    with connect() as c:
        row = c.execute(
            "SELECT value FROM app_state WHERE key='live_weight'"
        ).fetchone()

    data = json.loads(row["value"]) if row and row["value"] else None
    if not isinstance(data, dict):  # missing, or old plain-number format
        return {"weight": 0.0, "unit": "g", "connected": False, "age_s": None}

    age = time.time() - float(data.get("ts", 0))
    return {
        "weight": round(float(data["weight_g"]), 1),
        "unit": "g",
        "connected": age <= LIVE_WEIGHT_STALE_S,
        "age_s": round(age, 1),
    }

@app.get("/foods")
def foods():
    with connect() as c:
        rows = c.execute("SELECT class_name, display_name, state FROM foods ORDER BY display_name").fetchall()
    return [dict(r) for r in rows]

@app.post("/measurement")
async def measurement(
    weight_g: float = Form(...),
    image: UploadFile = File(...)
):
    if weight_g <= 0 or weight_g > 20000:
        raise HTTPException(
            400,
            "Weight must be greater than zero and no more than 20000 g"
        )

    if image.content_type and not image.content_type.startswith("image/"):
        raise HTTPException(400, "Please upload an image file")

    suffix = Path(image.filename or "capture.jpg").suffix.lower()

    if suffix not in [".jpg", ".jpeg", ".png", ".webp"]:
        raise HTTPException(
            400,
            "Supported image formats are JPG, PNG and WEBP"
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(image.file, tmp)
        image_path = Path(tmp.name)

    try:
        if image_path.stat().st_size == 0:
            raise HTTPException(400, "The uploaded image is empty")

        if image_path.stat().st_size > 10 * 1024 * 1024:
            raise HTTPException(400, "Image must be smaller than 10 MB")

        predictions = predict_foods(str(image_path))

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            500,
            f"Food recognition failed: {str(error)}"
        )

    finally:
        image_path.unlink(missing_ok=True)
        await image.close()

    enriched = []

    for prediction in predictions:
        food = get_food(prediction["class_name"])

        if food:
            enriched.append({
                **prediction,
                "display_name": food["display_name"],
                "state": food["state"],
                "nutrition": nutrition_for(food, weight_g)
            })

    result = {
        "weight_g": round(weight_g, 1),
        "predictions": enriched,
        "selected_class": enriched[0]["class_name"] if enriched else None,
        "message": "Confirm the prediction or select a food manually."
    }

    import json

    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO app_state(key,value) VALUES('latest',?)",
            (json.dumps(result),)
        )

    return result

@app.get("/latest")
def latest():
    import json
    with connect() as c:
        row = c.execute("SELECT value FROM app_state WHERE key='latest'").fetchone()
        cmd = c.execute("SELECT value FROM app_state WHERE key='pending_command'").fetchone()
    return {"result": json.loads(row["value"]) if row and row["value"] else None,
            "pending_command": cmd["value"] if cmd else ""}

@app.post("/select")
def select_food(req: SelectFoodRequest):
    import json
    with connect() as c:
        row = c.execute("SELECT value FROM app_state WHERE key='latest'").fetchone()
        if not row or not row["value"]:
            raise HTTPException(404, "No measurement yet")
        result = json.loads(row["value"])
        food = c.execute("SELECT * FROM foods WHERE class_name=?", (req.class_name,)).fetchone()
        if not food:
            raise HTTPException(404, "Food not found in database")
        food = dict(food)
        result["selected_class"] = req.class_name
        result["selected_food"] = {
            "class_name": req.class_name, "display_name": food["display_name"],
            "state": food["state"], "nutrition": nutrition_for(food, result["weight_g"])
        }
        c.execute("UPDATE app_state SET value=? WHERE key='latest'", (json.dumps(result),))
    return result

@app.post("/meal/add")
def add_to_meal(req: AddMealRequest):
    if req.weight_g <= 0:
        raise HTTPException(400, "Weight must be greater than zero")
    with connect() as c:
        food = c.execute("SELECT * FROM foods WHERE class_name=?", (req.class_name,)).fetchone()
        if not food:
            raise HTTPException(404, "Food not found")
        food = dict(food)
        meal = c.execute("SELECT id FROM meals ORDER BY id DESC LIMIT 1").fetchone()
        if not meal:
            c.execute("INSERT INTO meals DEFAULT VALUES")
            meal_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        else:
            meal_id = meal["id"]
        n = nutrition_for(food, req.weight_g)
        c.execute("""INSERT INTO meal_items(meal_id,food_id,weight_g,kcal,protein_g,carbs_g,fat_g)
                     VALUES(?,?,?,?,?,?,?)""",
                  (meal_id, food["id"], req.weight_g, n["kcal"], n["protein_g"], n["carbs_g"], n["fat_g"]))
    return {"added": True, "nutrition": n}

@app.get("/meal")
def meal():
    with connect() as c:
        meal_row = c.execute("SELECT id FROM meals ORDER BY id DESC LIMIT 1").fetchone()
        if not meal_row:
            return {"items": [], "totals": {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}}
        rows = c.execute("""SELECT mi.id AS item_id, f.display_name, f.state,
       mi.weight_g, mi.kcal, mi.protein_g,
       mi.carbs_g, mi.fat_g
                            FROM meal_items mi JOIN foods f ON f.id=mi.food_id
                            WHERE mi.meal_id=? ORDER BY mi.id""", (meal_row["id"],)).fetchall()
    items = [dict(r) for r in rows]
    totals = {k: round(sum(float(i[k]) for i in items), 2)
              for k in ("kcal", "protein_g", "carbs_g", "fat_g")}
    return {"items": items, "totals": totals}

@app.delete("/meal/item/{item_id}")
def remove_meal_item(item_id: int):
    with connect() as c:
        result = c.execute(
            "DELETE FROM meal_items WHERE id=?",
            (item_id,)
        )

        if result.rowcount == 0:
            raise HTTPException(404, "Meal item not found")

    return {"removed": True, "item_id": item_id}

@app.post("/tare")
def tare():
    with connect() as c:
        c.execute("UPDATE app_state SET value='tare' WHERE key='pending_command'")
    return {"queued": True, "command": "tare"}

@app.get("/command")
def command():
    with connect() as c:
        row = c.execute("SELECT value FROM app_state WHERE key='pending_command'").fetchone()
        value = row["value"] if row else ""
        if value:
            c.execute("UPDATE app_state SET value='' WHERE key='pending_command'")
    return {"command": value or ""}
