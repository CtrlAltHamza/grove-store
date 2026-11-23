import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


PRODUCTS = [
    ("trail-bottle", "Trail Bottle", "Drinkware", 2400, 18, "Insulated stainless steel, 600 ml. Keeps water cold on the move.", "bottle"),
    ("field-tote", "Field Tote", "Bags", 2900, 12, "Heavy cotton canvas with a roomy interior pocket.", "tote"),
    ("pocket-journal", "Pocket Journal", "Stationery", 1200, 26, "A6 recycled paper notebook with 96 ruled pages.", "journal"),
    ("daybreak-mug", "Daybreak Mug", "Drinkware", 1800, 9, "Glazed stoneware, 350 ml. Dishwasher safe.", "mug"),
]


class Store:
    def __init__(self, database_url=None, sqlite_path=None):
        self.database_url = database_url or os.getenv("DATABASE_URL")
        self.sqlite_path = sqlite_path or Path(__file__).with_name("shopbot.sqlite3")

    @contextmanager
    def connect(self):
        if self.database_url:
            connection = psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=5)
        else:
            connection = sqlite3.connect(self.sqlite_path, timeout=30)
            connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def execute(self, connection, sql, parameters=()):
        return connection.execute(sql.replace("?", "%s") if self.database_url else sql, parameters)

    def setup(self):
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS shop_products (
                sku TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
                cents INTEGER NOT NULL, stock INTEGER NOT NULL, description TEXT NOT NULL, art TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS shop_threads (
                user_id TEXT NOT NULL, thread_id TEXT NOT NULL, transcript TEXT NOT NULL,
                pending TEXT, PRIMARY KEY (user_id, thread_id)
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS shop_profiles (
                user_id TEXT PRIMARY KEY, preferences TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS shop_orders (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, sku TEXT NOT NULL, quantity INTEGER NOT NULL,
                total_cents INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            for product in PRODUCTS:
                self.execute(connection, "INSERT INTO shop_products VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", product)

    @contextmanager
    def conversation(self, user_id):
        with self.connect() as connection:
            if self.database_url:
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (user_id,))
            else:
                connection.execute("BEGIN IMMEDIATE")
            yield connection

    def catalog(self, connection):
        return [dict(row) for row in connection.execute("SELECT * FROM shop_products ORDER BY sku")]

    def thread(self, connection, user_id, thread_id):
        row = self.execute(connection, "SELECT transcript, pending FROM shop_threads WHERE user_id = ? AND thread_id = ?", (user_id, thread_id)).fetchone()
        if row is None:
            return [], None
        return json.loads(row["transcript"]), json.loads(row["pending"]) if row["pending"] else None

    def save_thread(self, connection, user_id, thread_id, history, pending):
        self.execute(connection, """INSERT INTO shop_threads VALUES (?, ?, ?, ?)
            ON CONFLICT (user_id, thread_id) DO UPDATE SET transcript = excluded.transcript, pending = excluded.pending""",
            (user_id, thread_id, json.dumps(history[-12:]), json.dumps(pending) if pending else None))

    def preference(self, connection, user_id):
        row = self.execute(connection, "SELECT preferences FROM shop_profiles WHERE user_id = ?", (user_id,)).fetchone()
        return row["preferences"] if row else ""

    def save_preference(self, connection, user_id, preference):
        self.execute(connection, """INSERT INTO shop_profiles VALUES (?, ?)
            ON CONFLICT (user_id) DO UPDATE SET preferences = excluded.preferences""", (user_id, preference))

    def order(self, connection, user_id, order_id, sku, quantity):
        row = self.execute(connection, """UPDATE shop_products SET stock = stock - ?
            WHERE sku = ? AND stock >= ? RETURNING name, cents""", (quantity, sku, quantity)).fetchone()
        if row is None:
            return None
        self.execute(connection, "INSERT INTO shop_orders (id, user_id, sku, quantity, total_cents) VALUES (?, ?, ?, ?, ?)",
                     (order_id, user_id, sku, quantity, row["cents"] * quantity))
        return dict(row)
