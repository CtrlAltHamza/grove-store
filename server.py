import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from anthropic import APIError, RateLimitError
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from agent import build_agent
from store import Store


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")


class ChatMessage(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:@-]+$")
    message: str = Field(min_length=1, max_length=2000)


class ChatRequest(ChatMessage):
    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:@-]+$")


class ShopperChatRequest(ChatMessage):
    model_config = ConfigDict(extra="forbid")


class ShopperSession(BaseModel):
    token: str


class Tokens(BaseModel):
    in_: int = Field(default=0, serialization_alias="in")
    out: int = 0


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict]
    tokens: Tokens


CATALOG_COLUMNS = ("sku", "name", "category", "price", "stock", "description", "art")
CATALOG_ART = {"bottle", "tote", "journal", "mug"}


async def read_catalog_csv(request: Request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 128 * 1024:
            raise HTTPException(413, "The catalog CSV must be under 128 KB.")
        data.extend(chunk)
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")), strict=True)
        if tuple(reader.fieldnames or ()) != CATALOG_COLUMNS:
            raise HTTPException(422, "Use the CSV template headers in the order shown.")
        products = {}
        for row in reader:
            if len(products) >= 100:
                raise HTTPException(422, "Import at most 100 products at once.")
            if None in row or any(value is None for value in row.values()):
                raise HTTPException(422, f"Line {reader.line_num} has the wrong number of columns.")
            sku, name, category, price, stock, description, art = (row[key].strip() for key in CATALOG_COLUMNS)
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", sku) or sku in products:
                raise HTTPException(422, f"Line {reader.line_num} needs a unique lowercase SKU.")
            if not (0 < len(name) <= 80 and 0 < len(category) <= 40 and 0 < len(description) <= 300):
                raise HTTPException(422, f"Line {reader.line_num} has an empty or oversized text field.")
            if not re.fullmatch(r"[0-9]{1,5}\.[0-9]{2}", price) or not re.fullmatch(r"[0-9]{1,5}", stock):
                raise HTTPException(422, f"Line {reader.line_num} needs a USD price with two decimals and a whole stock count.")
            if art not in CATALOG_ART:
                raise HTTPException(422, f"Line {reader.line_num} needs one of the art values in the template.")
            products[sku] = {"sku": sku, "name": name, "category": category, "cents": int(price.replace(".", "")),
                             "stock": int(stock), "description": description, "art": art}
    except (UnicodeError, csv.Error):
        raise HTTPException(422, "Upload a valid UTF-8 CSV file.") from None
    if not products:
        raise HTTPException(422, "Add at least one product row.")
    return list(products.values())


def sign_shopper_id(shopper_id):
    secret = os.getenv("SHOPBOT_SESSION_SECRET") or os.getenv("SHOPBOT_ADMIN_TOKEN")
    if not secret:
        raise HTTPException(503, "Shopper sessions are temporarily unavailable.")
    return hmac.digest(secret.encode(), f"grove-store/shopper/v1/{shopper_id}".encode(), "sha256").hex()


def create_app(store=None, model=None):
    store = store if store is not None else Store()
    chat = build_agent(store, os.getenv("ANTHROPIC_MODEL") or "claude-haiku-5-5", model)

    @asynccontextmanager
    async def lifespan(_app):
        if os.getenv("SHOPBOT_REQUIRE_POSTGRES") == "1" and not store.database_url:
            raise RuntimeError("DATABASE_URL is required for deployment.")
        store.setup()
        yield

    app = FastAPI(title="ShopBot", lifespan=lifespan)
    origins = [origin.strip() for origin in os.getenv("SHOPBOT_ALLOWED_ORIGINS", "").split(",") if origin.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "Authorization"])

    @app.get("/health", response_class=PlainTextResponse)
    def health():
        return "ok"

    @app.get("/catalog")
    def catalog():
        with store.connect() as connection:
            return store.catalog(connection)

    def run_chat(user_id, request: ChatMessage, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if not request.message.strip():
            raise HTTPException(422, "Enter a message.")
        # Encode both components unambiguously and keep caller-provided IDs out of checkpoint keys.
        key = hashlib.sha256(json.dumps([user_id, request.thread_id]).encode()).hexdigest()
        config = {"configurable": {"thread_id": key}}
        with store.conversation(user_id) as connection, store.checkpointer(connection) as saver:
            graph = chat.compile(checkpointer=saver)
            turn = {"message": request.message, "reply": None, "facts": {}, "tool_calls": [], "tokens": {"in": 0, "out": 0}}
            if saver.get_tuple(config) is None:
                history, pending = store.legacy_thread(connection, user_id, request.thread_id)
                turn.update(history=history[-12:], pending=pending)
            try:
                result = graph.invoke(turn, config, durability="exit", context={
                    "user_id": user_id, "connection": connection, "preference": store.preference(connection, user_id),
                })
            except RateLimitError:
                raise HTTPException(503, "The sales assistant is busy. Try again shortly.") from None
            except APIError:
                raise HTTPException(503, "The sales assistant is temporarily unavailable.") from None
            tokens = result["tokens"]
            return ChatResponse(reply=result["reply"], tool_calls=result["tool_calls"],
                                tokens=Tokens(in_=tokens["in"], out=tokens["out"]))

    @app.post("/chat", response_model=ChatResponse)
    def send_message(request: ChatRequest, response: Response):
        return run_chat(request.user_id, request, response)

    @app.post("/shopper/session", response_model=ShopperSession)
    def create_shopper_session(response: Response):
        shopper_id = uuid4().hex
        response.headers["Cache-Control"] = "no-store"
        return ShopperSession(token=f"{shopper_id}.{sign_shopper_id(shopper_id)}")

    @app.post("/shopper/chat", response_model=ChatResponse)
    def send_shopper_message(request: ShopperChatRequest, response: Response,
                             authorization: str | None = Header(default=None)):
        match = re.fullmatch(r"Bearer ([0-9a-f]{32})\.([0-9a-f]{64})", authorization or "")
        if not match or not secrets.compare_digest(match[2], sign_shopper_id(match[1])):
            raise HTTPException(401, "Your shopper session is invalid.", headers={"Cache-Control": "no-store"})
        # Public grading IDs cannot contain a slash, so they cannot address storefront records.
        return run_chat(f"shopper/{match[1]}", request, response)

    def require_admin(response: Response, authorization: str | None):
        token = os.getenv("SHOPBOT_ADMIN_TOKEN")
        if not token:
            raise HTTPException(503, "Set SHOPBOT_ADMIN_TOKEN to enable the dashboard.")
        if not secrets.compare_digest((authorization or "").encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "Enter the dashboard access token.")
        response.headers["Cache-Control"] = "no-store"

    @app.post("/admin/catalog/preview")
    async def preview_catalog(request: Request, response: Response, authorization: str | None = Header(default=None)):
        require_admin(response, authorization)
        return {"products": await read_catalog_csv(request)}

    @app.post("/admin/catalog/import")
    async def import_catalog(request: Request, response: Response, authorization: str | None = Header(default=None)):
        require_admin(response, authorization)
        products = await read_catalog_csv(request)
        # Run the synchronous database transaction outside the request event loop.
        if not await run_in_threadpool(store.upsert_products, products):
            raise HTTPException(422, "The store can contain at most 100 products.")
        return {"saved": len(products)}

    @app.get("/admin/overview")
    def overview(response: Response, authorization: str | None = Header(default=None)):
        require_admin(response, authorization)
        with store.connect() as connection:
            products = store.catalog(connection)
            summary = dict(connection.execute(
                "SELECT COUNT(*) AS order_count, COALESCE(SUM(total_cents), 0) AS order_total FROM shop_orders"
            ).fetchone())
            orders = [dict(row) for row in connection.execute("""SELECT o.id, p.name, o.quantity, o.total_cents, o.created_at
                FROM shop_orders o JOIN shop_products p ON p.sku = o.sku ORDER BY o.created_at DESC LIMIT 30""")]
        return {"products": products, "summary": summary, "orders": orders}

    @app.get("/", include_in_schema=False)
    def storefront():
        return FileResponse(ROOT / "web" / "index.html")

    @app.get("/admin", include_in_schema=False)
    def dashboard():
        return FileResponse(ROOT / "web" / "admin.html")

    app.mount("/assets", StaticFiles(directory=ROOT / "web" / "assets"), name="assets")
    return app


app = create_app()
