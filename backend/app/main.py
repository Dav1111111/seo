from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.router import v1_router
from app.database import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(
    title="Yandex Growth Control Tower",
    version="0.1.0",
    lifespan=lifespan,
)

import os

_allowed_origins = [
    "http://localhost:3000",
    "http://localhost",
    "http://c6b32ea267b.vps.myjino.ru",
]
# Add custom origin from env if set
_extra = os.getenv("CORS_ORIGINS", "")
if _extra:
    _allowed_origins.extend(o.strip() for o in _extra.split(",") if o.strip())

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v1_router, prefix="/api/v1")
