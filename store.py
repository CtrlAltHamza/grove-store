import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
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
            if self.database_url:
                # The backend connects as the table owner. Supabase API roles
                # have no row policies and cannot read or write these tables.
                connection.execute("ALTER TABLE shop_products ENABLE ROW LEVEL SECURITY")
                connection.execute("ALTER TABLE shop_threads ENABLE ROW LEVEL SECURITY")
                connection.execute("ALTER TABLE shop_profiles ENABLE ROW LEVEL SECURITY")
                connection.execute("ALTER TABLE shop_orders ENABLE ROW LEVEL SECURITY")
            for product in PRODUCTS:
                self.execute(connection, "INSERT INTO shop_products VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", product)

        if self.database_url:
            # LangGraph migrations include CREATE INDEX CONCURRENTLY.
            with psycopg.connect(self.database_url, autocommit=True, connect_timeout=5) as connection:
                connection.execute("SELECT pg_advisory_lock(1350104283)")
                PostgresSaver(connection).setup()
                connection.execute("ALTER TABLE checkpoint_migrations ENABLE ROW LEVEL SECURITY")
                connection.execute("ALTER TABLE checkpoints ENABLE ROW LEVEL SECURITY")
                connection.execute("ALTER TABLE checkpoint_blobs ENABLE ROW LEVEL SECURITY")
                connection.execute("ALTER TABLE checkpoint_writes ENABLE ROW LEVEL SECURITY")

    @contextmanager
    def checkpointer(self, connection):
        if self.database_url:
            yield PostgresSaver(connection)
        else:
            with SqliteSaver.from_conn_string(f"{self.sqlite_path}.checkpoints.sqlite3") as saver:
                yield saver

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

    def upsert_products(self, products):
        with self.conversation("admin/catalog-import") as connection:
            current = {row["sku"] for row in self.catalog(connection)}
            if len(current.union(product["sku"] for product in products)) > 100:
                return False
            for product in products:
                self.execute(connection, """INSERT INTO shop_products (sku, name, category, cents, stock, description, art)
                    VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (sku) DO UPDATE SET
                    name=excluded.name, category=excluded.category, cents=excluded.cents,
                    stock=excluded.stock, description=excluded.description, art=excluded.art""",
                    tuple(product[key] for key in ("sku", "name", "category", "cents", "stock", "description", "art")))
            return True

    def legacy_thread(self, connection, user_id, thread_id):
        row = self.execute(connection, "SELECT transcript, pending FROM shop_threads WHERE user_id = ? AND thread_id = ?", (user_id, thread_id)).fetchone()
        if row is None:
            return [], None
        return json.loads(row["transcript"]), json.loads(row["pending"]) if row["pending"] else None

    def preference(self, connection, user_id):
        row = self.execute(connection, "SELECT preferences FROM shop_profiles WHERE user_id = ?", (user_id,)).fetchone()
        return row["preferences"] if row else ""

    def save_preference(self, connection, user_id, preference):
        self.execute(connection, """INSERT INTO shop_profiles VALUES (?, ?)
            ON CONFLICT (user_id) DO UPDATE SET preferences = excluded.preferences""", (user_id, preference))

    def order(self, connection, user_id, order_id, sku, quantity, quoted_cents):
        if quoted_cents is None:
            return None
        row = self.execute(connection, """UPDATE shop_products SET stock = stock - ?
            WHERE sku = ? AND stock >= ? AND cents = ? RETURNING name, cents""",
            (quantity, sku, quantity, quoted_cents)).fetchone()
        if row is None:
            return None
        self.execute(connection, "INSERT INTO shop_orders (id, user_id, sku, quantity, total_cents) VALUES (?, ?, ?, ?, ?)",
                     (order_id, user_id, sku, quantity, row["cents"] * quantity))
        return dict(row)
