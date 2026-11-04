"""Банк (ПриватБанк «Автоклієнт») — лише читання. Тонкий шар над services/bank.py."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...services import bank
from ...services.common import Actor
from ..deps import get_actor, get_session

router = APIRouter(prefix="/api/bank", tags=["bank"])


@router.get("/status")
def status(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return bank.status(s, actor)


@router.post("/check")
def check(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return bank.check_connection(s, actor)


@router.get("/balance")
def balance(on: date, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return bank.balance(s, actor, on)


@router.get("/statement")
def statement(start: date, end: date, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return bank.statement(s, actor, start, end)


@router.get("/acquiring")
def acquiring(start: date, end: date, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return bank.acquiring(s, actor, start, end)


@router.get("/reconcile")
def reconcile(day: date, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return bank.reconcile_day(s, actor, day)
