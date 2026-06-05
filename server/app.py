"""FastAPI application factory.

Exposes ``create_app(pump_factory=None)`` so production code can inject a
real ``SyringePumpController`` and tests can inject a ``FakePump``. The
factory owns the lifespan: on startup it calls ``pump_factory()`` once
and stashes the result on ``app.state.pump``; on shutdown it calls the
pump's ``close()`` method if present.

Other ``app.state`` fields:
- ``pump_lock``: ``asyncio.Lock`` serializing every driver interaction.
- ``last_diagnose``: cached ``DiagnosticsReport`` (None until first
  ``GET /v1/diagnose`` succeeds).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from server.errors import register_exception_handlers
from server.routes import router
from sy01b import SyringePumpController

PumpFactory = Callable[[], Any]


def create_app(
    pump_factory: PumpFactory | None = None,
    *,
    config: SyringePumpController.Config | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if pump_factory is not None:
            app.state.pump = pump_factory()
        app.state.config = config
        app.state.pump_lock = asyncio.Lock()
        app.state.last_diagnose = None
        try:
            yield
        finally:
            pump = getattr(app.state, "pump", None)
            close = getattr(pump, "close", None)
            if callable(close):
                close()

    app = FastAPI(
        title="sy01b-server",
        version="0.1.0",
        description=(
            "HTTP bridge from a remote ESP32 client to the local "
            "SyringePumpController driver over /dev/ttyUSB1.\n\n"
            "The endpoints are grouped to mirror the firmware UI: every "
            "tab on the ESP32 maps to exactly one endpoint."
        ),
        openapi_tags=[
            {
                "name": "Discovery",
                "description": (
                    "Read-only probes used by the firmware on boot and "
                    "while idle. Safe to call repeatedly — never moves "
                    "the plunger or valve."
                ),
            },
            {
                "name": "Motion",
                "description": (
                    "Commands surfaced as buttons in the ESP32 firmware. "
                    "Each call holds the driver lock for the full "
                    "operation and replies with the resulting state."
                ),
            },
            {
                "name": "Low-level (deprecated)",
                "description": (
                    "Lower-level conveniences kept for back-compat. The "
                    "firmware does not use these — every UI action is "
                    "served by the Motion endpoints above."
                ),
            },
        ],
        lifespan=lifespan,
    )
    app.include_router(router)
    register_exception_handlers(app)
    return app
