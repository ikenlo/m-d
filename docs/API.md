# REST API

Інтерактивна документація: `/docs` (Swagger) на сервері, генерується з коду; вона завжди точніша за цей файл.
Тут — карта ендпоінтів і правила (стан 0.3.0).

## Загальні правила

- Авторизація: `POST /api/auth/login` → `token` (+ `idle_minutes`); далі `Authorization: Bearer <token>`.
  2FA: `login` повертає `{twofa: true, challenge_id, expires_in}` → `POST /api/auth/login/verify
  {challenge_id, code, remember, device_label}` → `token` (+ `device_token` для наступних `login`).
- Сесія: токен живе 30 хв без дій (`MDS_IDLE_MINUTES`), максимум 12 год (`MDS_TOKEN_TTL_HOURS`);
  панель продовжує його `POST /api/auth/refresh`, поки людина активна.
- Гроші — `int` копійки; ставки — bp; дата-час — ISO, локальний час салону (naive).
- Помилки: `{"error": "<код>", "detail": "<українською>"}`; 401 / 403 / 404 / 409 / 422 / 429.
- **Оптимістичне блокування:** сутності з `version` (записи, замовлення, зміни, виплати, витрати) приймають
  `version` у тілі (або `?version=` для DELETE); розбіжність → 409 `version_conflict` з `current_version` і `current`
  (поточний стан). Одночасний запис з двох сесій також дає 409.
- **Ідемпотентність:** закриття запису — поле `idempotency_key`; виплати й витрати — заголовок `Idempotency-Key`.
  Той самий ключ і запит → той самий результат; той самий ключ з іншим запитом → 409 `idempotency_conflict`.
  Ключі зберігаються 30 днів.
- **PIN «Фінансів»:** якщо увімкнено (`finance_pin_enabled`), фінансові ендпоінти директора вимагають заголовок
  `X-Finance-Token` (з `POST /api/auth/finance-verify`); інакше 403 `finance_pin_required` / `finance_pin_setup`.
- Права перевіряються в сервісах. Ролі — лише `director` і `master`. «Персонал» = майстер + директор.

## Вхід, сесії, безпека

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `POST /api/auth/login`, `POST /api/auth/login/verify` | усі | вхід, 2FA; журнал входів; 20 невдалих спроб з IP за 15 хв → 429 |
| `POST /api/auth/refresh` | усі | продовжити сесію (у межах 12 год) |
| `POST /api/auth/logout-all` | усі | завершити всі свої сесії |
| `GET /api/auth/me`, `POST /api/auth/change-password`, `POST /api/auth/telegram-code` | усі | профіль, пароль, код для `/link` |
| `GET/DELETE /api/auth/devices[?device_id=]` | усі | довірені пристрої |
| `POST /api/auth/finance-pin {pin, current_pin}`, `POST /api/auth/finance-verify {pin}` | директор | задати PIN; отримати `finance_token` (5 спроб / 15 хв) |
| `POST /api/users/{id}/reset-finance-pin` | інший директор | скинути PIN |
| `POST /api/users/{id}/revoke-sessions` | директор; сам користувач | завершити всі сесії людини |
| `GET /api/security/logins?user_id&success&limit` | директор | журнал входів (90 днів) |
| `GET/POST /api/users`, `PATCH /api/users/{id}` | директор | персонал, послуги, графік, `master_rate_bp` (у UI прихована; ≠ 5000 → `master_rate_warning`) |
| `POST /api/users/{id}/reset-password`, `POST /api/users/{id}/telegram-code` | директор | |

## Каталог і клієнти

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `GET/POST /api/services`, `PATCH /api/services/{id}` | читання — усі; зміна — директор | послуги (`price_from`, `needs_materials`) |
| `GET/POST /api/materials`, `PATCH /api/materials/{id}` | те саме | матеріали; `markup_bp` (підказка ціни, не входить у формулу) |
| `GET /api/clients`, `GET /api/clients/{id}` | персонал (майстер — свої) | клієнти, історія |
| `POST /api/clients`, `PATCH /api/clients/{id}`, `POST /api/clients/purge-no-phone` | директор | створити / змінити / прибрати без телефону |
| `GET /api/clients/base`, `/lookup`, `/duplicates`, `/analytics`, `/export.csv` | директор | клієнтська база |
| `GET/PATCH /api/clients/{id}/card`, `POST /api/clients/{id}/merge`, `POST /api/clients/{id}/anonymize` | директор | картка, об'єднання, знеособлення |
| `GET /api/callbacks`, `POST /api/callbacks/{id}/done` | персонал | заявки «передзвоніть» |

## Календар

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `GET /api/appointments?date_from&date_to&master_id` | персонал (майстер — свої) | календар (у кожному записі `version`, `price_from`, `needs_materials`) |
| `GET /api/availability`, `GET /api/availability/detail` | персонал | вільні слоти; швидкий запис |
| `POST /api/appointments`, `PATCH /api/appointments/{id}` | персонал | створення / перенесення. 409: `off_hours` (повтор із `confirm_off_hours`), `off_hours_forbidden`, `no_place`, `slot_taken` |
| `GET /api/appointments/{id}`, `POST /api/appointments/{id}/status` | персонал | `confirmed`, `in_progress`, `cancelled`, `no_show` |
| `GET /api/appointments/pending`, `POST …/{id}/decline-busy`, `POST …/{id}/propose` | персонал | непідтверджені записи |
| `POST /api/appointments/shift`, `GET /api/appointments/unclosed` | персонал | запізнення; незакриті замовлення |
| `POST /api/appointments/{id}/close` | майстер (свої), директор | **закрити замовлення**: `{amount (A), payment_method, price (P), status: completed\|unpaid, materials[], products[{product_id, qty}], idempotency_key, client_name}`. `price` змінюється лише для послуг «від» або директором |
| `GET/PUT /api/schedule…`, `POST /api/schedule/{id}/parity`, `/copy-week` | директор | графік роботи |

## Гроші

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `GET /api/orders?period\|date_from&date_to&master_id&status&materials_not_entered` | директор; майстер — свої (без `salon_earning`) | замовлення: `price_snapshot`, `discount`, `status`, `version`, `products` |
| `GET /api/orders/{id}` | директор; майстер — своє | + `history` (останні 5) і `history_total` |
| `GET /api/orders/needs-materials` | персонал | замовлення без матеріалів |
| `PATCH /api/orders/{id}` | директор; майстер — матеріали у своєму, поки зміна відкрита | виправлення з причиною (`price`, `amount`, `payment_method`, `materials`, `version`) |
| `POST /api/orders/{id}/mark-paid {payment_method, amount, reason}` | директор | `unpaid → completed` |
| `POST /api/orders/{id}/refund {reason}`, `POST /api/orders/{id}/cancel {reason}` | директор | повернення / анулювання (з товарами) |
| `PUT /api/orders/{id}/phone` | директор; майстер — свої, поки зміна відкрита | телефон клієнта |
| `POST /api/orders/{id}/products {product_id, qty}` | персонал (майстер — поки зміна відкрита) | додати товар (10% бонус майстру) |
| `POST /api/orders/products-only {master_id, payment_method, items, client_id?, idempotency_key?}` | персонал | продаж товарів без послуги |
| `POST /api/order-products/{id}/refund {reason}` | директор | повернення товару |
| `GET /api/shifts/current`, `POST /api/shifts/open`, `POST /api/shifts/close[?force=true]` | майстер / директор | зміна; 409 `incomplete_orders`; примусово — лише директор і лише з матеріалами (409 `materials_required`) |
| `GET /api/shifts`, `GET /api/shifts/{id}` | директор; майстер — свої | звіти змін (`view`: `master` без виручки й частки салону / `director`) |
| `GET/POST /api/payouts` | директор; майстер — свої | виплати; довільна — з `tag`: `advance`, `bonus`, `correction_overpayment` |
| `GET /api/bonuses?master_id`, `GET /api/bonuses/my`, `POST /api/bonuses/pay {master_id, until?}` | директор / майстер | бонус за товари (накопичено, історія, виплата) |
| `GET /api/finance/dashboard?period=day\|week\|month\|year` або `date_from&date_to` | директор (+PIN) | панель ([FINANCE.md](FINANCE.md) §5) + розрізи |
| `GET /api/finance/insights?period=…` | директор (+PIN) | порівняння з попереднім періодом, прогноз |
| `POST /api/analytics/recommend?period&refresh` | директор (+PIN) | рекомендації: правила або локальна llama (відкат на правила), кеш 1 год |
| `GET /api/finance/logic-stats` | директор (+PIN) | середні цифри місяця для «Логіки системи» |
| `GET /api/finance/my[?master_id]` | майстер; директор | «Мої фінанси»: зміна, місяць, 7 днів, топ-3, історія 20 |
| `GET /api/exports/{finance\|orders}.{pdf\|xlsx\|csv}?period…` | директор (+PIN) | експорт звітів |
| `GET/POST /api/expenses`, `PATCH/DELETE /api/expenses/{id}` | директор | витрати; `DELETE` = архів (`archived_at`) |

## Склад

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `GET /api/stock`, `GET /api/stock/moves[?material_id\|product_id]` | персонал / директор | залишки; журнал руху (`item_type`, `reason`, `unit_price`, користувач, замовлення) |
| `POST /api/stock/{id}/receive`, `/adjust`, `/writeoff` | директор | матеріали: прихід (середня собівартість), інвентаризація, списання |
| `POST /api/stock/inventory {items: [{material_id\|product_id, actual}], note}` | директор | інвентаризація кількох позицій |
| `GET/POST /api/products`, `PATCH /api/products/{id}`, `POST /api/products/{id}/receive` | читання — персонал; зміна — директор | товари |

## Комунальні і генератор

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `GET/POST /api/utilities/readings`, `PATCH …/{id}`, `POST …/{id}/confirm` | директор | лічильник, тариф, підтвердження |
| `GET /api/utilities/summary` | директор | місяць |
| `GET /api/generator`, `POST /api/generator/start`, `/stop` | персонал | перемикач |
| `GET/POST /api/generator/runs`, `PATCH …/{id}`, `POST /api/generator/hours`, `/motohours`, `GET /api/generator/history` | директор | запуски, мотогодини |

Розподілу комунальних на майстрів немає: салон платить усе ([FINANCE.md](FINANCE.md) §12).

## Податки, звіти

`GET /api/reports/income`, `/income.csv`, `/declaration`, `/declaration.zip`; `GET/POST /api/taxes/rates`,
`GET /api/taxes/fop`, `POST /api/taxes/fop/accrue`, `POST /api/taxes/fop/{id}/correct`, `POST /api/taxes/payments`;
`GET /api/employees`, `PUT /api/employees/{user_id}`; `GET/POST /api/payroll`, `GET /api/payroll/calc`,
`POST /api/payroll/{id}/correct`, `POST /api/payroll/payments`, `GET /api/payroll/history/{user_id}`,
`GET /api/payroll/quarter`, `/quarter.csv`. Усе — директор. Дохід = лише оплачені (`completed`) замовлення.

## Сповіщення, відпустка

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `GET /api/alerts[?unread=true]`, `POST /api/alerts/read {ids?}` | усі | дзвіночок |
| `POST /api/alerts/{id}/handled` | директор | «оброблено» — для обох директорів |
| `GET/PUT /api/alerts/settings` | директор | вимкнути важливі (критичні не вимикаються) |
| `GET /api/settings/vacation-mode`, `POST /api/settings/vacation-mode {active, days ≤ 14}` | усі / директор | режим «Ми у відпустці» |
| `GET/POST /api/permission-requests`, `POST …/{id}/withdraw` | майстер | запит дозволу (7 дій), відкликання |
| `POST …/{id}/approve`, `POST …/{id}/reject` | директор | перший, хто вирішив, виграє; дія виконується один раз |

## Система

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `GET /api/health` | — | стан і `version` |
| `GET/PUT /api/settings` | читання — усі; зміна — директор | налаштування (`reports_hidden`, `finance_pin_enabled`, `alerts_digest_hour`…) |
| `GET /api/journal/ledger`, `/audit`, `/feed` | директор | журнали |
| `GET/POST /api/backups`, `GET /api/backups/{name}` | директор | копії; завантажити копію (третя копія на ПК) |
| `GET /api/gdrive`, `POST /api/gdrive/connect`, `/backup-now`, `/disconnect`, `GET /api/gdrive/callback` | директор | Google Drive (Service Account або OAuth) |
| `GET /api/sync?since=<rev>` | персонал | живе оновлення |
| `POST /api/bug-report`, `GET /api/perf`, `POST /api/perf/rum` | усі / директор | |
| `GET /api/card-terminal/status`, `POST /api/card-payments`, `…/{id}/check`, `…/{id}/cancel`, `GET /api/appointments/{id}/card-payments` | персонал | «Термінал»: у 0.3.0 лише ручний режим (D-6c); `…/mock-complete` — лише `MDS_ENV=test` |
| `GET /api/sms/*`, `POST /api/sms/campaigns?dry_run=1` | директор | історія і розрахунок аудиторії; надсилання вимкнено (D-6a, 409 `sms_disabled`) |

## `/api/ext/v1` — MDs Manager

Лише читання (D-5). Ключ — заголовок `X-API-Key` = `MDS_MANAGER_API_KEY` (порівняння за сталий час).
Ключ не задано → 404 «Інтеграцію вимкнено»; немає/невірний → 401. JWT персоналу тут не приймається, а ключ
Manager не відкриває решту `/api`. Персональних даних клієнтів (імена, телефони, дати народження, Telegram,
Instagram, нотатки) немає. Гроші — копійки. Період `date_from`…`date_to` (за замовчуванням останні 30 днів,
не довше 366); списки — `limit` (1–1000, 500) і `offset`, відповідь `{total, limit, offset, items}`.
Сервіс — `services/integration.py` (actor `integration`).

| Метод і шлях | Параметри | Відповідь |
|---|---|---|
| `GET /api/ext/v1/orders` | період, `status`, `limit`, `offset` | замовлення за датою закриття: `id`, `created_at`, `status`, `master_id`, `service_id`, `service_name`, `client_id`, `payment_method`, `price_snapshot`, `amount`, `discount`, `materials_cost`, `master_earning`, `salon_earning`, `products_total`, `products_cost` |
| `GET /api/ext/v1/stock-moves` | період, `item_type` (`material`/`product`), `limit`, `offset` | рух складу: `ts`, `item_type`, `name`, `unit`, `reason`, `qty`, `unit_price`, `cost`, `order_id` |
| `GET /api/ext/v1/products` | — | товари: ціни закупівлі/продажу, націнка, залишок, `active` |
| `GET /api/ext/v1/services` | — | каталог послуг: ціна, «від», тривалість |
| `GET /api/ext/v1/clients` | `limit`, `offset` | знеособлено: `id`, `created_at`, `source`, `marketing_consent`, `blocked`, `segment`, `visits`, `spent`, `first_visit`, `last_visit` |
| `GET /api/ext/v1/daily` | період | по днях: `orders`, `revenue`, `price_total`, `discounts`, `masters_share`, `materials`, `salon`, `cash`, `noncash`, `products_revenue/cost/bonus`, `unpaid_count/amount`, `expenses` (лише `completed`; `unpaid` — окремо, Q-8) |

Не робимо (D-5): жодних `POST/PATCH/DELETE`, запису на прийом, скасувань. Рекомендації Manager показує у себе (Q-10).
