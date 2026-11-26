"""Re-import data/foods.csv into the database (run after editing the CSV).

The backend also does this automatically on startup if the foods table is empty.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.food_import import CSV_PATH, import_foods

print(f"Successfully imported {import_foods()} foods from {CSV_PATH}")
