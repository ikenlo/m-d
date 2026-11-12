"""Публічні вебхуки Meta (Instagram, Facebook): лише прийом у чергу webhook_queue (D-6a)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from ...services import webhooks
from ...services.common import Actor
from ..deps import get_session

log = logging.getLogger("mds")
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
MAX_BODY = 1024 * 1024


@router.get("/meta", include_in_schema=False)
def meta_verify(request: Request):
    challenge = webhooks.verify_challenge(dict(request.query_params), request.app.state.config.meta_verify_token)
    if challenge is None:
        return PlainTextResponse("Forbidden", status_code=403)
    return PlainTextResponse(challenge, status_code=200)


@router.post("/meta", include_in_schema=False)
async def meta_event(request: Request, s: Session = Depends(get_session)):
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return JSONResponse({"error": "too_large", "detail": "Завеликий вебхук"}, status_code=413)
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return JSONResponse({"error": "too_large", "detail": "Завеликий вебхук"}, status_code=413)
    webhooks.check_signature(raw, request.headers.get("x-hub-signature-256"), request.app.state.config.meta_app_secret)
    row = webhooks.receive(s, Actor.system(), raw)
    log.info("Вебхук отримано: source=%s, uuid=%s", row.source, row.uuid)
    return {"ok": True}
