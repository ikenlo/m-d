# CLAUDE.md — правила розробки MDS Connect

Внутрішня система **одного** салону M&D. Не додавати мультитенантність, підписки, білінг, реєстрацію салонів.
Мова інтерфейсу, повідомлень помилок і бота — **українська**. Коментарі в коді — українською.

## Команди

```bash
cd server
pip install -r requirements-dev.txt
python -m pytest                 # усі тести (обов'язково перед комітом)
ruff check mds tests             # лінтер
node --check mds/web/static/app.js
python -m mds init / run / serve / bot / migrate / backup
alembic revision --autogenerate -m "опис"   # після зміни models.py (з каталогу server/)
```

## Архітектура (коротко) — детально в docs/ARCHITECTURE.md

- `mds/services/*` — **уся** бізнес-логіка і перевірка прав. API (`mds/api`) і бот (`mds/bot`) лише
  перетворюють запит → виклик сервісу → відповідь. Не дублюйте правила в роутерах/UI.
- Сервісні функції: `(session, actor, ...)`, не роблять `commit` — транзакцією керує `Database.session()`.
- Права перевіряються в сервісі (`actor.require(...)`, `_check_access`), а не лише на клієнті.
- Помилки — `mds.errors.*` (ValidationError 422, Conflict 409, Forbidden 403, NotFound 404).

## Інваріанти — не порушувати

1. Гроші — `int` копійки; відсотки — bp (`5000` = 50%). Ніяких `float` у розрахунках.
2. `amount == master_earning + materials_cost + salon_earning` для кожного замовлення.
3. Формула поділу — тільки в `services/finance.py::compute_split`.
4. `ledger` — лише додавання. Виправлення = сторно + нове проведення (`orders.correct_order`).
5. Кожна зміна важливих даних пише `audit(...)` (було/стало/хто/причина).
6. Захист від подвійних записів: `_lock_master` перед перевіркою перетинів (`bookings._check_slot`).
7. Закриття замовлення — атомарний `UPDATE ... WHERE status IN CLOSABLE`; `UNIQUE(orders.appointment_id)`.
8. Час — через `mds.clock.now()` (локальний час салону, naive datetime). Не використовуйте `datetime.now()` у логіці.
9. Зміна `models.py` ⇒ нова міграція Alembic у `migrations/versions/` (тест `test_migrations_match_models` впаде без неї).
10. Повідомлення в Telegram — лише через чергу `services/notifications.py` (outbox), не напряму з сервісів.
11. Веб-панель: DOM лише через `h()`, без `innerHTML` з даними користувача. Бот: `html.escape` для даних.

## Як додавати функцію

1. Сервіс + тест у `tests/test_*.py` (фіксований час — фікстура `fixed_clock`, дані — `seed`).
2. Роутер у `mds/api/routers/` + тест у `tests/test_api.py`.
3. UI: `VIEWS.<name>` у `mds/web/static/app.js` + пункт у `NAV[роль]`. Бот: обробник у `bot/handlers.py::build_router`
   + тест у `tests/test_bot.py` (харнес `BotHarness` проганяє справжній диспетчер aiogram).
4. Оновити документацію в `docs/` (особливо `FINANCE.md` для змін у грошах).

## Процес змін (тестування та погодження)

- Ніколи не пушити напряму в `main`. Кожна зміна — окрема гілка + Pull Request.
- CI (`.github/workflows/ci.yml`) має бути зеленим: ruff + перевірка JS + усі тести.
- PR зливає лише людина (директор/відповідальний) після перегляду. Автоматичні зміни без погодження заборонені.
- Зміни фінансових правил — лише за явним запитом директора, з тестами на конкретні суми.

## Застосунки (apps/)

- `apps/android` — WebView-оболонка (Java, без сторонніх бібліотек), `apps/windows` — Electron. Логіки в них немає:
  вони відкривають веб-панель; нові функції робляться на сервері/в `app.js`.
- Збірка й перевірка: `.github/workflows/apps.yml` (APK + запуск на емуляторі, EXE на windows-latest). Деталі — `docs/APPS.md`.
- Ключ підпису Android — лише в секретах GitHub, ніколи не комітити `.jks`.
## Версії

- Кожне оновлення (PR у `main`) — нова версія `0.1.N`: підняти `__version__` у `server/mds/__init__.py`
  і `version` у `server/pyproject.toml`, додати зверху `CHANGELOG.md` розділ `## [0.1.N] — дата`.
  Тест `test_version.py` перевіряє узгодженість.
- Після злиття `.github/workflows/release.yml` сам створює тег `v0.1.N` і GitHub Release (архів версії).

## Фіскалізація і податки (docs/FISCALIZATION.md)

- Провайдери ПРРО — лише через `services/fiscal_providers.py`; у тестах — `FakePRROProvider` (`set_provider`), ніколи не справжні чеки.
- Чек створюється в транзакції закриття замовлення (`fiscal.enqueue_sell`); фіскалізованим вважається лише `done`.
- Ключі провайдера — лише змінні оточення (`MDS_CHECKBOX_TEST_*` для тестової каси, `MDS_CHECKBOX_*` для бойової), не в БД і не в логах.
- Режими mock / test / production; бойовий — лише з `MDS_FISCAL_PRODUCTION_ALLOWED=yes` на сервері (окреме погодження). Налаштування — `/api/fiscal/config`.
- Податкові ставки й мінімальна зарплата — записи `tax_rates` з датами; не зашивати в код. Юридична зарплата ≠ поділ 50/50.
