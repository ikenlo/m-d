"""Звіт про доходи для податкової декларації — тонкий шар над services/tax_report.py."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...services import tax_report
from ...services.common import Actor
from ..deps import get_actor, get_session

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/income")
def income(year: int, quarter: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return tax_report.income_report(s, actor, year, quarter)


@router.get("/income.csv")
def income_csv(year: int, quarter: int | None = None, detail: str = "day",
               actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    body, filename = tax_report.income_csv(s, actor, year, quarter, detail)
    return Response(body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})
