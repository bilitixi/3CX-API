from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import call_control
from app.core.auth import require_api_token
from app.core.logging import configure_logging, logger
from app.services.escalation import queue_answer_watcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("app_startup")

    queue_answer_watcher.start()

    yield

    queue_answer_watcher.stop()
    logger.info("app_shutdown")


app = FastAPI(
    title="3CX Call Control API",
    description="Dial-into-queue call control workflow: rings a source DN first, "
    "then bridges it into a 3CX queue once answered.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(call_control.router, prefix="/api/threecx", dependencies=[Depends(require_api_token)])


@app.get("/health")
async def health():
    return {"status": "ok"}
