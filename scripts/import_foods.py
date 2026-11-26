
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.db import init_db, connect

csv_path = ROOT / "data" / "foods.csv"


def get_state(food_id):
    food_id = food_id.lower()

    if "raw" in food_id:
        return "raw"
    elif "cooked" in food_id:
        return "cooked"
    elif "as sold" in food_id:
        return "as sold"
    elif "whole" in food_id:
        return "whole"

    return "unspecified"


init_db()

imported = 0

with csv_path.open(newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)

    required_columns = {
        "food_id", "name", "calories",
        "protein", "carbs", "fat"
    }

    if not required_columns.issubset(reader.fieldnames or []):
        raise ValueError(
            f"CSV must contain these columns: {required_columns}"
        )

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
                food_id,
                name,
                get_state(food_id),
                float(row["calories"]),
                float(row["protein"]),
                float(row["carbs"]),
                float(row["fat"]),
                "custom_csv",
                food_id
            ))

            imported += 1

print(f"Successfully imported {imported} foods from {csv_path}")
