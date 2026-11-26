"""Load data/foods.csv into the foods table.

Used by scripts/import_foods.py and, automatically, by the backend on
startup when the foods table is empty (e.g. right after a fresh clone).
"""

import csv
from pathlib import Path

from .db import init_db, connect

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "foods.csv"
REQUIRED_COLUMNS = {"food_id", "name", "calories", "protein", "carbs", "fat"}


def get_state(food_id):
    food_id = food_id.lower()
    if "raw" in food_id:
        return "raw"
    if "cooked" in food_id:
        return "cooked"
    if "as sold" in food_id:
        return "as sold"
    if "whole" in food_id:
        return "whole"
    return "unspecified"


def import_foods(csv_path=CSV_PATH):
    """Insert or update every food in the CSV. Returns the number imported."""
    init_db()
    imported = 0
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not REQUIRED_COLUMNS.issubset(reader.fieldnames or []):
            raise ValueError(f"CSV must contain these columns: {REQUIRED_COLUMNS}")

        with connect() as conn:
            for row in reader:
                food_id = row["food_id"].strip()
                name = row["name"].strip()
                if not food_id or not name:
                    continue
                conn.execute("""
                    INSERT INTO foods (
                        class_name, display_name, state,
                        kcal, protein_g, carbs_g, fat_g,
                        source, source_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(class_name) DO UPDATE SET
                        display_name = excluded.display_name,
                        state = excluded.state,
                        kcal = excluded.kcal,
                        protein_g = excluded.protein_g,
                        carbs_g = excluded.carbs_g,
                        fat_g = excluded.fat_g,
                        source = excluded.source,
                        source_id = excluded.source_id
                """, (
                    food_id, name, get_state(food_id),
                    float(row["calories"]), float(row["protein"]),
                    float(row["carbs"]), float(row["fat"]),
                    "custom_csv", food_id,
                ))
                imported += 1
    return imported


def import_if_empty():
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM foods").fetchone()[0]
    if count == 0:
        n = import_foods()
        print(f"Foods table was empty: imported {n} foods from {CSV_PATH.name}")
