
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("ADDGRAM_DB", Path(__file__).with_name("addgram.db")))


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = connect()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS foods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_name TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            state TEXT NOT NULL,
            kcal REAL NOT NULL,
            protein_g REAL NOT NULL,
            carbs_g REAL NOT NULL,
            fat_g REAL NOT NULL,
            source TEXT,
            source_id TEXT
        );

        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS meal_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_id INTEGER NOT NULL REFERENCES meals(id),
            food_id INTEGER NOT NULL REFERENCES foods(id),
            weight_g REAL NOT NULL,
            kcal REAL NOT NULL,
            protein_g REAL NOT NULL,
            carbs_g REAL NOT NULL,
            fat_g REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        INSERT OR IGNORE INTO app_state(key, value)
        VALUES ('pending_command', '');
        """)

        row = conn.execute(
            "SELECT id FROM meals ORDER BY id DESC LIMIT 1"
        ).fetchone()

        if row is None:
            conn.execute("INSERT INTO meals DEFAULT VALUES")

        conn.commit()
    finally:
        conn.close()
