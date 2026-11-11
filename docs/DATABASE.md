# База даних

Джерело правди — `server/mds/models.py` і міграції `server/migrations/versions/`. Тут — довідник і правила.
**[0.3.0+]** / **[0.3.0−]** — що додається / прибирається в релізі канону ([FINANCE.md](FINANCE.md), [CANON.md](CANON.md)).

## Правила

- SQLite за замовчуванням (`data/mds.db`), PRAGMA: `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=30000`.
  PostgreSQL — через `MDS_DATABASE_URL` (вбудовані бекапи лише для SQLite).
- Одна транзакція на запит (`Database.session()`); сервіси не роблять `commit`.
- Будь-яка зміна `models.py` ⇒ нова міграція Alembic; тест `test_migrations_match_models` перевіряє збіг.
  У SQLite зміна/перейменування колонок — через `op.batch_alter_table`.
- Гроші — `Integer` (копійки); кількості складу — тисячні (`*_milli`); ставки — bp.
- Час — naive datetime у локальному часі салону (`clock.now()`).
- Фінансові таблиці не видаляються і не «чистяться»: `orders`, `ledger`, `audit_log` — лише додавання;
  витрати — архівування. Чиститься лише `idempotency_keys` (30 днів) і `login_events` (90 днів).
- Від подвійних дій захищає БД, а не лише код: `UNIQUE` + атомарний `UPDATE … WHERE`.

## Таблиці (поточні)

| Група | Таблиця | Ключове |
|---|---|---|
| Персонал | `users` | `role` (`director`, `master`; **[0.3.0−]** `admin` → `director`), `token_version`, `failed_logins`/`locked_until`, `bot_pin_hash`, `booking_lock` (лічильник для блокування рядка), графік JSON. **[0.3.0]** `share_bp` → `master_rate_bp` (5000); `materials_mode` застаріле |
| | `master_services` | які послуги виконує майстер |
| Каталог | `services` | ціна (коп.), тривалість, `price_from` («від»), `needs_materials`, розділ/тип/вид |
| | `materials` | одиниця г/мл/шт, упаковка (`pack_size`), `unit_cost` (ціна упаковки), `stock_milli`, мінімум. **[0.3.0+]** `markup_bp` |
| Склад | `stock_moves` | рух матеріалів: `ts`, `kind`, `qty_milli` ±, `cost`, `order_id`, `user_id`, `note` |
| Клієнти | `clients` | телефони (`phone` UNIQUE, може бути NULL; `phone2`), Telegram, Instagram, згода, сегментні поля |
| | `callback_requests` | заявки «передзвоніть» |
| Календар | `appointments` | майстер, послуга, `starts_at`/`ends_at`, `price`, `status`, `source`. **[0.3.0+]** `version` |
| | `schedule_days` | винятки графіка на дату, UNIQUE(master, day) |
| Гроші | `orders` | UNIQUE `appointment_id`, UNIQUE `idempotency_key`, `amount`, `materials_cost`, `payment_method`, знімок `share_bp`/`materials_mode`, `master_earning`, `salon_earning`, `revision`, хто закрив/редагував |
| | `order_materials` | рядки матеріалів: `qty` (тисячні), `unit_cost`, `total` — знімок на момент закриття |
| | `shifts` | `open → closed → paid`, `report` (знімок JSON), `payout_due` |
| | `payouts` | UNIQUE `shift_id` (може бути NULL — довільна виплата), `cash_shift_id` (взято з каси), `method` |
| | `expenses` | категорія, сума, дата, спосіб оплати, `is_deleted` (архів) |
| | `ledger` | журнал проведень (лише додавання): `kind`, `ref_type/ref_id`, суми amount/master/salon/materials |
| | `card_payments` | оплати «Терміналом»: статус, RRN, код авторизації, маска картки, чек |
| Комунальні | `meter_readings` | місяць (UNIQUE), показники (тисячні кВт·год), тариф, вартість, сплачено, витрата |
| | `generator_runs` | запуск: знімок л/год, ціни бензину, амортизації; `running` UNIQUE (лише один працює) |
| | `master_charges` | **[0.3.0−]** списання з майстрів; лише читання до 0.3.1 (Q-1) |
| Податки | `tax_rates`, `tax_accruals`, `tax_payments`, `payroll_accruals`, `payroll_payments`, `employee_profiles` | ставки з датами; нарахування зі сторно (`reverses_id`, `dedupe_key`) |
| Журнали | `audit_log` | хто, коли, сутність, дія, було/стало, причина |
| | `notifications` | черга Telegram: `sent_at`, `attempts` (до 5), `last_error` |
| Вхід | `login_challenges`, `trusted_devices` | 2FA-коди (хеш), довірені пристрої (хеш токена, `token_version`) |
| SMS | `sms_campaigns`, `sms_messages` | **[0.3.0]** надсилання вимикається; дані лишаються |
| Інше | `settings` | ключ → JSON; значення перевіряє `common.update_settings` |
| | `expense_files` | не використовується (функцію відкочено); видалити або використати — окреме рішення |
| Гілка ext | `appointment_attribution` (міграція 0025) | лише в `feat/ext-api-md-manager`, не злита |

Статуси: запис `pending → confirmed → in_progress → completed | cancelled | no_show`;
зміна `open → closed → paid`.

## Зміни 0.3.0 (фаза 2)

| Зміна | Деталі |
|---|---|
| `orders.price_snapshot` INT NOT NULL | старі замовлення: `= amount` (знижка 0; D-2) |
| `orders.master_rate_bp` INT NOT NULL DEFAULT 5000 | старі: `= share_bp` |
| `orders.status` | `completed` (усі старі) / `unpaid` / `cancelled` / `refunded` |
| `orders.materials_not_entered` BOOL | старі: обчислити з `services.needs_materials` і відсутності рядків |
| `version` INT DEFAULT 1 | `appointments`, `orders`, `shifts`, `payouts`, `expenses` |
| `order_materials.unit_price_snapshot` | з наявного `unit_cost` (копіювання/перейменування) |
| `payouts.tag` | `advance` / `bonus` / `product_bonus` / `correction_overpayment`; старі — NULL |
| `users.master_rate_bp` | перейменування `share_bp` з копіюванням |
| `users.role = 'admin'` → `'director'` | D-4, з записом в `audit_log` |
| `idempotency_keys` | `key` PK, `response_json`, `created_at` |
| `products`, `order_products`, `bonus_accruals` | [FINANCE.md](FINANCE.md) §6. Продаж без послуги потребує замовлення без запису (`orders.appointment_id` NULL — рішення в пакеті 2.3) |
| журнал руху складу | розширити `stock_moves` (`item_type`, ціна одиниці, причини канону) або нова `stock_movements` — рішення в пакеті 2.13 |
| `permission_requests` | id, user_id, action, payload_json, status, created_at, resolved_at, resolved_by, note |
| `login_events` | user_id, ip, user_agent, method, success, ts; 90 днів |
| `users.finance_pin_hash`, лічильник спроб PIN бота | D-7 — у БД, не в пам'яті |
| `expenses.archived_at` | замість/поряд із `is_deleted` |
| `materials_mode`, `orders.share_bp`, `master_charges` | лишаються до 0.3.1, потім видаляються |

Порядок міграцій: гілка `feat/ext-api-md-manager` має `0025`. Якщо її частина (лише читання, D-5) зливається —
до міграцій фази 2, щоб не було двох «голів» Alembic.

## Чутливі дані

Імена й телефони клієнтів, дати народження, РНОКПП працівників (`employee_profiles.tin`), хеші паролів/PIN/кодів,
дані карткових оплат (без повного номера картки). Шифрування на диску — [SECURITY.md](SECURITY.md).
Бекапи й відновлення — [OPERATIONS.md](OPERATIONS.md).
