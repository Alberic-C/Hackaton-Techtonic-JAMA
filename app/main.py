from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .gemini_client import LLMError
from .routers import reviews, sessions, webhook

STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Sophia", version="0.1.0", lifespan=lifespan)
app.include_router(webhook.router)
app.include_router(sessions.router)
app.include_router(reviews.router)


@app.exception_handler(LLMError)
async def llm_error_handler(_: Request, exc: LLMError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
        resp.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
    return resp


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
