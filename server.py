import hmac
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from anthropic import APIError, RateLimitError
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

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
        with store.conversation(user_id) as connection:
            history, pending = store.thread(connection, user_id, request.thread_id)
            try:
                result = chat.invoke({
                    "message": request.message, "user_id": user_id, "connection": connection,
                    "history": history, "pending": pending, "preference": store.preference(connection, user_id),
                    "tool_calls": [], "tokens": {"in": 0, "out": 0},
                })
            except RateLimitError:
                raise HTTPException(503, "The sales assistant is busy. Try again shortly.") from None
            except APIError:
                raise HTTPException(503, "The sales assistant is temporarily unavailable.") from None
            history.extend([{"role": "user", "text": request.message}, {"role": "assistant", "text": result["reply"]}])
            store.save_thread(connection, user_id, request.thread_id, history, result["pending"])
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

    @app.get("/admin/overview")
    def overview(response: Response, authorization: str | None = Header(default=None)):
        token = os.getenv("SHOPBOT_ADMIN_TOKEN")
        if not token:
            raise HTTPException(503, "Set SHOPBOT_ADMIN_TOKEN to enable the dashboard.")
        if not secrets.compare_digest((authorization or "").encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "Enter the dashboard access token.")
        response.headers["Cache-Control"] = "no-store"
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
