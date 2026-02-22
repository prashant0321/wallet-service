"""
FastAPI application entry point.
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from app.config import settings
from app.models import Base
from app.database import engine
from app.routers.wallet import router as wallet_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create all tables on startup (idempotent — won't overwrite existing data)."""
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
## Wallet Service

A high-performance closed-loop virtual currency service for gaming platforms and loyalty systems.

### Features
- **Double-entry bookkeeping** — every credit has a matching debit, the ledger always balances.
- **Pessimistic locking** — `SELECT ... FOR UPDATE` eliminates race conditions under heavy concurrency.
- **Idempotency** — pass an `Idempotency-Key` header to safely retry any mutating request.
- **ACID transactions** — PostgreSQL guarantees atomicity; no partial updates.
- **Non-negative balances** — enforced at both the application layer and as a DB `CHECK` constraint.

### Three Core Flows
1. **Top-up** — user purchases credits (real money → virtual credits via Treasury).
2. **Bonus** — system grants free credits (Bonus Pool → user).
3. **Spend** — user buys in-app item (user → Revenue wallet).
    """,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(wallet_router)


# ── Global exception handler ──────────────────────────────────────────────────
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "code": "INTERNAL_ERROR", "message": str(exc)},
    )


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"], summary="Health check")
def health():
    return {"status": "healthy", "service": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/", tags=["System"], summary="Root")
def root():
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
