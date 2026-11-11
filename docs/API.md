# REST API

Інтерактивна документація: `/docs` (Swagger) на сервері, генерується з коду; вона завжди точніша за цей файл.
Тут — карта ендпоінтів і правила. Позначки: **[0.3.0+]** з'являється в релізі канону, **[0.3.0−]** видаляється.

## Загальні правила

- Авторизація: `POST /api/auth/login` → `token`; далі `Authorization: Bearer <token>`.
  2FA: `login` повертає `{twofa: true, challenge_id, expires_in}` → `POST /api/auth/login/verify
  {challenge_id, code, remember, device_label}` → `token` (+ `device_token` для наступних `login`).
- Гроші — `int` копійки; ставки — bp; дата-час — ISO, локальний час салону (naive).
- Помилки: `{"error": "<код>", "detail": "<українською>"}`; 401 / 403 / 404 / 409 / 422.
- **[0.3.0+]** 409 через `version` повертає ще й `current` — поточний стан сутності. Зміни сутностей з `version`
  приймають `version` у тілі.
- Права перевіряються в сервісах; колонка «Хто» нижче — для орієнтиру. «Персонал» = майстер + директор
  (**[0.3.0]** без адміністратора).

## Вхід і користувачі

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `POST /api/auth/login`, `POST /api/auth/login/verify` | усі | вхід, 2FA |
| `GET /api/auth/me` | усі | поточний користувач (+ `open_shift_id`) |
| `POST /api/auth/change-password` | усі | зміна пароля (завершує інші сесії) |
| `POST /api/auth/telegram-code` | усі | код для `/link` у боті |
| `GET/DELETE /api/auth/devices[?device_id=]` | усі | довірені пристрої |
| `GET/POST /api/users`, `PATCH /api/users/{id}` | директор | персонал, послуги, графік. **[0.3.0−]** поля `share_bp`, `materials_mode`; роль `admin` |
| `POST /api/users/{id}/reset-password`, `POST /api/users/{id}/telegram-code` | директор | |
| **[0.3.0+]** `POST /api/auth/finance-pin`, `POST /api/auth/finance-verify`, `POST /api/users/{id}/reset-finance-pin` | директор | PIN вкладки «Фінанси» |
| **[0.3.0+]** `GET /api/security/logins`, `POST /api/users/{id}/revoke-sessions` | директор | журнал входів, відкликання сесій |

## Каталог і клієнти

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `GET/POST /api/services`, `PATCH /api/services/{id}` | читання — усі; зміна — директор | послуги |
| `GET/POST /api/materials`, `PATCH /api/materials/{id}` | те саме | матеріали (**[0.3.0+]** `markup_bp`) |
| `GET /api/clients`, `GET /api/clients/{id}` | персонал (майстер — свої) | клієнти, історія |
| `POST /api/clients`, `PATCH /api/clients/{id}` | директор | створити / змінити, блокування `{blocked, blocked_reason}` |
| `POST /api/clients/purge-no-phone` | директор (сервіс перевіряє права) | видалити клієнтів без телефону (`dry_run` — лише підрахунок) |
| `GET /api/clients/base`, `/lookup`, `/duplicates`, `/analytics`, `/export.csv` | директор | клієнтська база |
| `GET/PATCH /api/clients/{id}/card`, `POST /api/clients/{id}/merge`, `POST /api/clients/{id}/anonymize` | директор | картка, об'єднання, знеособлення |
| `GET /api/callbacks`, `POST /api/callbacks/{id}/done` | персонал | заявки «передзвоніть» |

## Календар

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `GET /api/appointments?date_from&date_to&master_id` | персонал (майстер — свої) | календар |
| `GET /api/availability`, `GET /api/availability/detail` | персонал | вільні слоти; швидкий запис (`slots`, `off_hours`, `working`, `shift_open`, `off_policy`) |
| `POST /api/appointments`, `PATCH /api/appointments/{id}` | персонал | створення / перенесення. 409: `off_hours` (повтор із `confirm_off_hours`), `off_hours_forbidden`, `no_place`, перетин |
| `GET /api/appointments/{id}` | персонал | |
| `POST /api/appointments/{id}/status` | персонал | `confirmed`, `in_progress`, `cancelled`, `no_show` |
| `GET /api/appointments/pending`, `POST …/{id}/decline-busy`, `POST …/{id}/propose` | персонал | непідтверджені записи |
| `POST /api/appointments/shift` | персонал (майстер — свої) | зсунути записи через запізнення |
| `GET /api/appointments/unclosed` | персонал | час минув, замовлення не закрито |
| `POST /api/appointments/{id}/close` | майстер (свої), директор | **закрити замовлення** `{amount, payment_method, materials[], idempotency_key, client_name}`; **[0.3.0+]** `price` (P, D-3), `status` (`completed`/`unpaid`), `products[]` |
| `GET/PUT /api/schedule…`, `POST /api/schedule/{id}/parity`, `/copy-week` | директор | графік роботи |

## Гроші

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `GET /api/orders`, `GET /api/orders/{id}` | директор; майстер — свої | замовлення (`missing`, `history`). **[0.3.0+]** фільтри `status`, `materials_not_entered` |
| `GET /api/orders/needs-materials` | персонал | замовлення без матеріалів |
| `PATCH /api/orders/{id}` | директор; майстер — матеріали у своєму, поки зміна відкрита | виправлення з причиною |
| `PUT /api/orders/{id}/phone` | директор; майстер — свої, поки зміна відкрита | телефон клієнта |
| **[0.3.0+]** `POST /api/orders/{id}/mark-paid`, `/refund`, `/cancel` | директор | `unpaid → completed`; повернення; скасування |
| **[0.3.0+]** `POST /api/orders/{id}/products`, `POST /api/orders/products-only` | персонал (поки зміна відкрита) | товари |
| `GET /api/shifts/current`, `POST /api/shifts/open`, `POST /api/shifts/close[?force=true]` | майстер / директор | зміна; 409 `incomplete_orders` |
| `GET /api/shifts`, `GET /api/shifts/{id}` | директор; майстер — свої | звіти змін (**[0.3.0]** окремий вигляд для майстра) |
| `GET/POST /api/payouts` | директор; майстер — свої | виплати; **[0.3.0+]** `tag` |
| **[0.3.0+]** `POST /api/bonuses/pay`, `GET /api/bonuses/my` | директор / майстер | бонуси за товари |
| `GET /api/finance/dashboard?period=day\|week\|month\|year` або `date_from&date_to` | директор | панель ([FINANCE.md](FINANCE.md) §5) |
| `GET /api/finance/insights?period=…` | директор | порівняння з попереднім періодом, прогноз (лишається) |
| `GET /api/finance/my` | майстер | «Мої фінанси» |
| **[0.3.0+]** `GET /api/finance/logic-stats` | директор | середні цифри для «Логіки системи» |
| **[0.3.0+]** `POST /api/analytics/recommend` | директор | рекомендації (rules → llama), кеш 1 год |
| **[0.3.0+]** `GET /api/reports/{name}.pdf\|.xlsx\|.csv` | директор | експорт звітів |
| `GET/POST /api/expenses`, `PATCH/DELETE /api/expenses/{id}` | директор | витрати; `DELETE` = архів |

## Склад

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `GET /api/stock`, `GET /api/stock/moves` | персонал | залишки, рух |
| `POST /api/stock/{id}/receive`, `/adjust`, `/writeoff` | директор | прихід, інвентаризація, списання (**[0.3.0+]** також `POST /api/stock/inventory`) |

## Комунальні і генератор

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `GET/POST /api/utilities/readings`, `PATCH …/{id}`, `POST …/{id}/confirm` | директор | лічильник, тариф, підтвердження |
| `GET /api/utilities/summary` | директор | місяць |
| **[0.3.0−]** `GET /api/utilities/allocation`, `POST /api/utilities/allocate`, `POST /api/utilities/reverse`, `GET /api/utilities/charges` | — | розподіл на майстрів видаляється |
| `GET /api/generator`, `POST /api/generator/start`, `/stop` | персонал | перемикач |
| `GET/POST /api/generator/runs`, `PATCH …/{id}`, `POST /api/generator/hours`, `/motohours`, `GET /api/generator/history` | директор | запуски, мотогодини |

## Податки, звіти

`GET /api/reports/income`, `/income.csv`, `/declaration`, `/declaration.zip`; `GET/POST /api/taxes/rates`,
`GET /api/taxes/fop`, `POST /api/taxes/fop/accrue`, `POST /api/taxes/fop/{id}/correct`, `POST /api/taxes/payments`;
`GET /api/employees`, `PUT /api/employees/{user_id}`; `GET/POST /api/payroll`, `GET /api/payroll/calc`,
`POST /api/payroll/{id}/correct`, `POST /api/payroll/payments`, `GET /api/payroll/history/{user_id}`,
`GET /api/payroll/quarter`, `/quarter.csv`. Усе — директор.

## Система

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `GET /api/health` | — | стан і `version` |
| `GET/PUT /api/settings` | читання — усі; зміна — директор | налаштування. **[0.3.0+]** `POST /api/settings/vacation-mode` |
| `GET /api/journal/ledger`, `/audit`, `/feed` | директор | журнали |
| `GET/POST /api/backups`, `GET /api/gdrive`, `POST /api/gdrive/connect`, `/backup-now`, `/disconnect`, `GET /api/gdrive/callback` | директор | бекапи |
| `GET /api/sync?since=<rev>` | персонал | живе оновлення |
| `POST /api/bug-report` | усі | повідомлення про помилку директору |
| `GET /api/perf`, `POST /api/perf/rum` | директор / усі | швидкодія |
| `GET /api/card-terminal/status`, `POST /api/card-payments`, `…/{id}/check`, `…/{id}/cancel`, `GET /api/appointments/{id}/card-payments` | персонал | «Термінал» (**[0.3.0]** лише ручний режим; `…/mock-complete` — лише тестове середовище) |
| `GET /api/sms/*`, `POST /api/sms/campaigns`, `POST /api/sms/test` | директор | **[0.3.0−]** надсилання вимикається (D-6a) |
| **[0.3.0+]** `POST /api/permission-requests`, `…/{id}/approve`, `…/{id}/reject`, `…/{id}/withdraw` | майстер / директор | режим відпустки |

## `/api/ext/v1` — MDs Manager

Ключ у заголовку `X-MDS-Integration-Token` (змінна `MDS_INTEGRATION_TOKEN`; без неї → 404). JWT персоналу не діє.
Код поки лише в гілці `feat/ext-api-md-manager` (не злита). У 0.3.0 — **лише читання** (D-5):

| Метод і шлях | Що повертає |
|---|---|
| `GET /api/ext/v1/catalog` | активні послуги (ціни в коп.) і майстри |
| `GET /api/ext/v1/slots?service_id&date_from&date_to[&master_id]` | вільний час (≤ 31 день) |
| `GET /api/ext/v1/visits?date_from&date_to` | візити: статус, ціна, оплата, частка салону, атрибуція |
| `GET /api/ext/v1/ad-spend?date_from&date_to` | витрати «Реклама» |
| **[0.3.0+]** `GET /api/ext/v1/purchases`, `/expenses`, `/consumption`, `/stock` | ціни закупівлі, витрати за послугами/майстрами, витрата матеріалів, залишки |

**Не робимо:** `POST /api/ext/v1/appointments`, `…/lookup`, `…/{id}/cancel` (з гілки прибираються, D-5).
