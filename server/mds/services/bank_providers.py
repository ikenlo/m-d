"""Банк (ПриватБанк, API «Автоклієнт» v3.0.0) — ЛИШЕ ЧИТАННЯ.

Специфікація: «Опис API для взаємодії з серверною частиною Автоклієнта, версія 3.0.0» (docs/PRIVATBANK.md).
Принципи:
- Дозволені лише запити з білого списку `ALLOWED` (баланси, виписка, еквайрингова виписка, серверні дати).
  Платежі, підписи, зарплатні відомості тощо **не реалізовано і заборонено** на рівні коду.
- Токен — лише змінна оточення `MDS_PRIVAT_TOKEN`; не потрапляє в БД, журнали, `repr` і повідомлення помилок.
- Не частіше 1 запиту на секунду (рекомендація банку).
- Суми — у копійках (`int`), розбір через `Decimal`, без `float`-арифметики.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable

BASE_URL = "https://acp.privatbank.ua"
TOKEN_ENV = "MDS_PRIVAT_TOKEN"
LIVE_ALLOWED_ENV = "MDS_BANK_LIVE_ALLOWED"
MODES = ("off", "mock", "live")

# Білий список: (метод, шлях). Усе інше — відмова до будь-якого мережевого запиту.
ALLOWED: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/api/statements/settings"),
    ("GET", "/api/statements/balance"),
    ("GET", "/api/statements/balance/interim"),
    ("GET", "/api/statements/balance/final"),
    ("GET", "/api/statements/transactions"),
    ("GET", "/api/statements/transactions/interim"),
    ("GET", "/api/statements/transactions/final"),
    # POST, але лише читання: еквайрингова виписка за терміналами
    ("POST", "/api/equiring/statements"),
})

STATEMENT_LIMIT = 100  # рекомендовано банком «не більше ніж 100»
ACQ_PAGE_SIZE = 100  # numberRecords за замовчуванням у банку
MAX_PAGES = 200  # запобіжник від нескінченної пагінації
MIN_INTERVAL_S = 1.0  # «не частіше 1 запиту в секунду»


# ------------------------------------------------------------------ помилки
class BankError(Exception):
    """Базова помилка банку. Повідомлення ніколи не містить токена."""


class BankAuthError(BankError):
    """401/403: токен неправильний або застосунок вимкнено / сервіс обмежено в «Приват24 для бізнесу»."""


class BankRejected(BankError):
    """400 та інші 4xx: некоректний запит."""


class BankUnavailable(BankError):
    """5xx, тайм-аут, мережа, регламентні роботи (phase ≠ WRK, work_balance = Y)."""


class BankForbiddenOperation(BankError):
    """Спроба викликати не дозволений (не read-only) ендпоінт."""


# ------------------------------------------------------------------ нормалізовані записи
@dataclass
class BankBalance:
    account: str
    currency: str
    balance_in: int
    balance_out: int
    turnover_debit: int
    turnover_credit: int
    last_move: str | None
    is_final: bool | None


@dataclass
class BankTxn:
    key: str  # REF + "/" + REFN — ідентифікатор унікальності за специфікацією
    direction: str  # "in" (TRANTYPE=C) | "out" (TRANTYPE=D)
    amount: int  # копійки, завжди > 0; напрям — у direction
    amount_uah: int
    currency: str
    status: str  # p проводиться | t сторнована | r проведена | n забракована
    real: str | None  # FL_REAL: r | i
    value_date: str | None  # DAT_OD
    booked_at: str | None  # DATE_TIME_DAT_OD_TIM_P
    purpose: str
    counterparty: str
    counterparty_code: str | None
    counterparty_account: str | None
    ref: str
    refn: str
    bank_id: str | None  # ID
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def booked(self) -> bool:
        return self.status == "r"


@dataclass
class AcqTxn:
    key: str  # див. acq_key()
    at: str | None  # Date (дата/час операції)
    posted: str | None  # TranPost
    settle_date: str | None  # TranRetPos (за прикладом — дата зарахування; ПІДТВЕРДИТИ В БАНКУ)
    amount: int  # Tran_Amt, копійки, зі знаком (від'ємна — повернення/сторно; ПІДТВЕРДИТИ)
    fee: int  # Acq_Amt (за прикладом 10.00 = 0.13 + 9.87 — комісія; ПІДТВЕРДИТИ)
    net: int  # Ret_Amt (до зарахування; ПІДТВЕРДИТИ)
    extra_fee: int  # Tran_Fee
    total: int  # Summ
    currency: str | None  # Curr (980 = UAH)
    rrn: str | None  # Extr_Rrn
    approval: str | None  # Tran_Approval
    terminal: str | None  # Ident_Ter
    card_mask: str | None  # Pan
    invoice: str | None  # Tran_Invoice
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def is_refund(self) -> bool:
        return self.amount < 0


def to_kop(value: Any) -> int:
    """'0.01' / -10.0 / '1 234,50' → копійки (int). Порожнє → 0."""
    if value is None or value == "":
        return 0
    s = str(value).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise BankRejected(f"Некоректна сума у відповіді банку: {value!r}") from e
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def acq_key(r: dict) -> str:
    """Ключ операції еквайрингу. У специфікації ключ унікальності НЕ описано (питання до банку),
    тому беремо RRN + код авторизації + дату/час + суму (повернення має ту саму RRN, але іншу суму/знак)."""
    return "|".join(str(r.get(k) or "") for k in ("Extr_Rrn", "Tran_Approval", "Date", "Tran_Amt", "Ident_Ter"))


def parse_txn(r: dict) -> BankTxn:
    trantype = str(r.get("TRANTYPE") or "").upper()
    if trantype not in ("C", "D"):
        raise BankRejected(f"Невідомий TRANTYPE у виписці: {trantype!r}")
    ref, refn = str(r.get("REF") or ""), str(r.get("REFN") or "")
    return BankTxn(
        key=f"{ref}/{refn}", direction="in" if trantype == "C" else "out",
        amount=abs(to_kop(r.get("SUM"))), amount_uah=abs(to_kop(r.get("SUM_E"))),
        currency=str(r.get("CCY") or ""), status=str(r.get("PR_PR") or ""), real=r.get("FL_REAL"),
        value_date=r.get("DAT_OD"), booked_at=r.get("DATE_TIME_DAT_OD_TIM_P"), purpose=str(r.get("OSND") or ""),
        counterparty=str(r.get("AUT_CNTR_NAM") or ""), counterparty_code=r.get("AUT_CNTR_CRF"),
        counterparty_account=r.get("AUT_CNTR_ACC"), ref=ref, refn=refn,
        bank_id=str(r["ID"]) if r.get("ID") is not None else None, raw=r,
    )


def parse_acq(r: dict) -> AcqTxn:
    return AcqTxn(
        key=acq_key(r), at=r.get("Date"), posted=r.get("TranPost"), settle_date=r.get("TranRetPos"),
        amount=to_kop(r.get("Tran_Amt")), fee=to_kop(r.get("Acq_Amt")), net=to_kop(r.get("Ret_Amt")),
        extra_fee=to_kop(r.get("Tran_Fee")), total=to_kop(r.get("Summ")),
        currency=str(r["Curr"]) if r.get("Curr") is not None else None,
        rrn=r.get("Extr_Rrn"), approval=r.get("Tran_Approval"), terminal=r.get("Ident_Ter"),
        card_mask=r.get("Pan"), invoice=r.get("Tran_Invoice"), raw=r,
    )


def parse_balance(r: dict) -> BankBalance:
    return BankBalance(
        account=str(r.get("acc") or ""), currency=str(r.get("currency") or ""),
        balance_in=to_kop(r.get("balanceIn")), balance_out=to_kop(r.get("balanceOut")),
        turnover_debit=to_kop(r.get("turnoverDebt")), turnover_credit=to_kop(r.get("turnoverCred")),
        last_move=r.get("dpd"), is_final=r.get("is_final_bal"),
    )


def as_dict(obj) -> dict:
    d = asdict(obj)
    d.pop("raw", None)
    return d


# ------------------------------------------------------------------ провайдер
class BankProvider:
    name = "base"

    def server_settings(self) -> dict: ...
    def balances(self, account: str, start: date, end: date) -> list[BankBalance]: ...
    def transactions(self, account: str, start: date, end: date) -> list[BankTxn]: ...
    def acquiring(self, account: str, day: date) -> list[AcqTxn]: ...


HttpFn = Callable[[str, str, dict, bytes | None, float], tuple[int, dict, bytes]]


def _urllib_http(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — лише https BASE_URL
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() or b""


class PrivatAutoclientProvider(BankProvider):
    """Справжній клієнт «Автоклієнта». У тестах `http` підмінюється емулятором — мережі немає."""

    name = "privat"

    def __init__(self, token: str, *, base_url: str = BASE_URL, http: HttpFn | None = None, timeout: float = 30.0,
                 user_agent: str = "MDS-Connect", sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic):
        if not token:
            raise BankAuthError(f"Не задано токен банку (змінна сервера {TOKEN_ENV})")
        if not base_url.startswith("https://"):
            raise BankRejected("Адреса банку має бути https://")
        self._token = token
        self.base_url = base_url.rstrip("/")
        self._http = http or _urllib_http
        self.timeout = timeout
        self.user_agent = user_agent
        self._sleep, self._mono = sleep, monotonic
        self._last = None
        self.calls = 0

    def __repr__(self) -> str:  # токен не показуємо ніколи
        return f"PrivatAutoclientProvider(base_url={self.base_url!r}, token=***)"

    # ---- транспорт
    def _throttle(self) -> None:
        if self._last is not None:
            wait = MIN_INTERVAL_S - (self._mono() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._mono()

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        if (method, path) not in ALLOWED:
            raise BankForbiddenOperation(f"Заборонено: {method} {path} — інтеграція з банком лише читає дані")
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        headers = {
            "User-Agent": self.user_agent,
            "token": self._token,
            # специфікація: підтримуються utf8 і cp1251; явно просимо utf8 (ПИТАННЯ ДО БАНКУ №1)
            "Content-Type": "application/json;charset=utf8",
            "Accept": "application/json",
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        self._throttle()
        self.calls += 1
        try:
            status, resp_headers, raw = self._http(method, url, headers, data, self.timeout)
        except (TimeoutError, OSError) as e:
            raise BankUnavailable(f"Банк недоступний ({type(e).__name__})") from None
        payload = _decode(raw, resp_headers)
        if status in (401, 403):
            raise BankAuthError(_err_text(status, payload, "Немає доступу: перевірте токен і обмеження сервісів"))
        if status >= 500:
            raise BankUnavailable(_err_text(status, payload, "Тимчасова помилка банку"))
        if status >= 400:
            raise BankRejected(_err_text(status, payload, "Некоректний запит"))
        if not isinstance(payload, dict):
            raise BankRejected("Відповідь банку не є JSON-об'єктом")
        if str(payload.get("status", "SUCCESS")).upper() == "ERROR":
            raise BankRejected(_err_text(status, payload, "Банк повернув помилку"))
        return payload

    def _paged(self, path: str, params: dict, items_key: str) -> list[dict]:
        out: list[dict] = []
        follow = None
        for _ in range(MAX_PAGES):
            page = self._request("GET", path, {**params, "followId": follow, "limit": STATEMENT_LIMIT})
            out.extend(page.get(items_key) or [])
            if not page.get("exist_next_page"):
                return out
            follow = page.get("next_page_id")
            if not follow:
                raise BankRejected("exist_next_page = true, але немає next_page_id")
        raise BankRejected("Забагато сторінок у відповіді банку")

    # ---- ендпоінти
    def server_settings(self) -> dict:
        return self._request("GET", "/api/statements/settings").get("settings") or {}

    def ensure_working(self) -> dict:
        st = self.server_settings()
        if st.get("phase") not in (None, "WRK") or st.get("work_balance") == "Y":
            raise BankUnavailable("У банку регламентні роботи (phase/work_balance) — спробуйте пізніше")
        return st

    def balances(self, account: str, start: date, end: date) -> list[BankBalance]:
        rows = self._paged("/api/statements/balance",
                           {"acc": account, "startDate": _dmy(start), "endDate": _dmy(end)}, "balances")
        return [parse_balance(r) for r in rows]

    def transactions(self, account: str, start: date, end: date) -> list[BankTxn]:
        rows = self._paged("/api/statements/transactions",
                           {"acc": account, "startDate": _dmy(start), "endDate": _dmy(end)}, "transactions")
        return _dedupe([parse_txn(r) for r in rows])

    def acquiring(self, account: str, day: date) -> list[AcqTxn]:
        """Один календарний день (обмеження банку: різниця дат ≤ 1 дня). dateFrom = dateTo = day."""
        out: list[AcqTxn] = []
        for page in range(1, MAX_PAGES + 1):
            resp = self._request("POST", "/api/equiring/statements", body={
                "account": account, "dateFrom": day.isoformat(), "dateTo": day.isoformat(), "page": page,
                "numberRecords": ACQ_PAGE_SIZE, "statementsDateType": "TRANSACTION_DATE",
            })
            out.extend(parse_acq(r) for r in (resp.get("dataCollections") or []))
            if not resp.get("existNextPage"):
                return _dedupe(out)
        raise BankRejected("Забагато сторінок еквайрингової виписки")


class FakeBankProvider(BankProvider):
    """Імітація банку для тестів і режиму mock. Мережі немає, дані — в пам'яті."""

    name = "mock"

    def __init__(self):
        self.settings = {"phase": "WRK", "work_balance": "N"}
        self.txns: list[dict] = []  # сирі записи у форматі банку
        self.acq: list[dict] = []
        self.balance_rows: list[dict] = []
        self.fail: BankError | None = None
        self.calls: list[tuple] = []

    def _check(self, op):
        self.calls.append(op)
        if self.fail:
            raise self.fail

    def server_settings(self) -> dict:
        self._check(("settings",))
        return dict(self.settings)

    def ensure_working(self) -> dict:
        st = self.server_settings()
        if st.get("phase") != "WRK" or st.get("work_balance") == "Y":
            raise BankUnavailable("У банку регламентні роботи (phase/work_balance) — спробуйте пізніше")
        return st

    def balances(self, account, start, end):
        self._check(("balances", account, start, end))
        return [parse_balance(r) for r in self.balance_rows]

    def transactions(self, account, start, end):
        self._check(("transactions", account, start, end))
        res = []
        for r in self.txns:
            d = datetime.strptime(r["DAT_OD"], "%d.%m.%Y").date()
            if start <= d <= end:
                res.append(parse_txn(r))
        return _dedupe(res)

    def acquiring(self, account, day):
        self._check(("acquiring", account, day))
        return _dedupe([parse_acq(r) for r in self.acq if str(r.get("Date", ""))[:10] == day.isoformat()])

    # ---- зручні конструктори даних для тестів (формат як у специфікації)
    def add_card(self, at: datetime, amount_kop: int, fee_kop: int, *, rrn: str, approval: str = "000000",
                 settle: date | None = None, terminal: str = "TERM0001") -> dict:
        def m(k):
            return float(Decimal(k) / 100)
        r = {"Page_Num": 1, "Ident_Ter": terminal, "Term_St": "A", "Date": at.strftime("%Y-%m-%dT%H:%M:%S"),
             "TranPost": at.date().isoformat(), "Tran_Invoice": None, "Pan": "545708****224923",
             "Tran_Approval": approval, "Extr_Rrn": rrn, "Curr": "980", "Tran_Amt": m(amount_kop),
             "Acq_Amt": m(fee_kop), "Ret_Amt": m(amount_kop - fee_kop), "Tran_Fee": 0.0, "Summ": m(amount_kop),
             "TranRetPos": (settle or (at.date() + timedelta(days=1))).isoformat()}
        self.acq.append(r)
        return r

    def add_txn(self, on: date, amount_kop: int, *, direction: str = "in", ref: str, refn: str = "1",
                purpose: str = "", status: str = "r", counterparty: str = "") -> dict:
        r = {"TRANTYPE": "C" if direction == "in" else "D", "SUM": f"{Decimal(amount_kop) / 100:.2f}",
             "SUM_E": f"{Decimal(amount_kop) / 100:.2f}", "CCY": "UAH", "PR_PR": status, "FL_REAL": "r",
             "DAT_OD": on.strftime("%d.%m.%Y"), "DATE_TIME_DAT_OD_TIM_P": on.strftime("%d.%m.%Y 10:00:00"),
             "OSND": purpose, "AUT_CNTR_NAM": counterparty, "REF": ref, "REFN": refn, "ID": ref}
        self.txns.append(r)
        return r


# ------------------------------------------------------------------ вибір провайдера
_override: BankProvider | None = None


def set_provider(p: BankProvider | None) -> None:
    """Лише для тестів: підмінити провайдера для будь-якого режиму."""
    global _override
    _override = p


_mock_singleton = FakeBankProvider()


def live_allowed() -> bool:
    return os.environ.get(LIVE_ALLOWED_ENV, "").strip().lower() == "yes"


def token_configured() -> bool:
    return bool(os.environ.get(TOKEN_ENV, "").strip())


def get_provider(mode: str) -> BankProvider:
    if mode not in MODES or mode == "off":
        raise BankRejected("Інтеграцію з банком вимкнено")
    if _override is not None:
        return _override
    if mode == "mock":
        return _mock_singleton
    if not live_allowed():
        raise BankForbiddenOperation(f"Реальне підключення до банку заблоковано (потрібно {LIVE_ALLOWED_ENV}=yes на сервері)")
    from .. import __version__

    return PrivatAutoclientProvider(os.environ.get(TOKEN_ENV, "").strip(), user_agent=f"MDS-Connect/{__version__}")


# ------------------------------------------------------------------ допоміжне
def _dmy(d: date) -> str:
    return d.strftime("%d-%m-%Y")  # формат специфікації: ДД-ММ-РРРР


def _decode(raw: bytes, headers: dict) -> Any:
    if not raw:
        return {}
    ctype = next((v for k, v in headers.items() if k.lower() == "content-type"), "") or ""
    enc = "cp1251" if "1251" in ctype.lower() else "utf-8"
    try:
        text = raw.decode(enc)
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return {"message": text[:200]}


def _err_text(status: int, payload: Any, default: str) -> str:
    msg = payload.get("message") if isinstance(payload, dict) else None
    code = payload.get("serviceCode") or payload.get("code") if isinstance(payload, dict) else None
    parts = [f"HTTP {status}", default if not msg else str(msg)[:200]]
    if code:
        parts.append(f"код {code}")
    return " · ".join(parts)


def _dedupe(items: list) -> list:
    seen, out = set(), []
    for it in items:
        if it.key in seen:
            continue
        seen.add(it.key)
        out.append(it)
    return out
