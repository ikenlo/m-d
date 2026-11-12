# Контекст: Інтеграція MDs Manager ↔ MDs Connect

## Що це і навіщо

**MDS Connect** — операційна система салону (записи, замовлення, гроші, склад, бот), працює на Railway 24/7 і є
єдиним джерелом правди для цих даних. **MDs Manager** ([ikenlo/md-manager](https://github.com/ikenlo/md-manager)) —
окремий продукт на локальному ПК директора: AI-аналітика, рекомендації, SMS і соцмережі (Instagram, Facebook).
Connect дає Manager дані й буферизує вхідні події Meta, а Manager їх аналізує й обробляє.

## Архітектура (гібрид)

```
Meta (Instagram, Facebook) ──POST /api/webhooks/meta──▶ Connect (Railway, 24/7) ──▶ webhook_queue
                                                            ▲
MDs Manager (локальний ПК, може бути вимкнений) ───GET /api/ext/v1/* (pull, X-API-Key)
                                               ───POST /api/ext/v1/webhooks/{uuid}/ack
```

- Meta вимагає відповіді протягом 5 с і публічної HTTPS-адреси. ПК директора може бути вимкнений, тож вебхуки приймає
  Connect і зберігає в черзі `webhook_queue`, нічого не обробляючи.
- Manager сам ходить у Connect (pull): дані — раз на годину, черга вебхуків — коли Manager увімкнений.
- Connect нічого не надсилає ні Manager, ні Meta, ні клієнтам.

## Rule D-5 (з CANON.md)

Manager **лише читає** дані Connect і повертає рекомендації; жодних записів на прийом, замовлень, скасувань.
**Єдиний виняток (12.11.2026, 0.3.1):** ack черги вебхуків — Manager змінює лише статус запису `webhook_queue`.
Вебхуки Meta приймає Connect, обробляє Manager (D-6a). Деталі — [CANON.md](CANON.md) §1 (D-5, D-6a) і §7.

## API Contract (Connect → Manager)

Заголовок `X-API-Key` = `MDS_MANAGER_API_KEY`. Ключ не задано на сервері → 404 «Інтеграцію вимкнено»; немає або
невірний → 401. Гроші — копійки (`int`), час — локальний час салону без часового поясу (ISO 8601). Повний опис полів —
[API.md](API.md) §`/api/ext/v1`.

| Метод і шлях | Що повертає |
|---|---|
| `GET /api/ext/v1/orders` | замовлення за період: ціна, сума, знижка, матеріали, частки майстра й салону, товари |
| `GET /api/ext/v1/products` | товари: ціни закупівлі й продажу, націнка, залишок |
| `GET /api/ext/v1/services` | каталог послуг: ціна, «від», тривалість |
| `GET /api/ext/v1/clients` | клієнти знеособлено (без імен і телефонів): сегмент, візити, сума |
| `GET /api/ext/v1/stock-moves` | рух складу: прихід, витрата, продаж, інвентаризація |
| `GET /api/ext/v1/daily` | підсумки по днях: виручка, частки, каса/безготівка, неоплачені, витрати |
| `GET /api/ext/v1/webhooks` | `{webhooks: [{uuid, source, payload, received_at}], count}` — найстаріші `pending` (`limit` 1–100, 50), стають `processing` |
| `POST /api/ext/v1/webhooks/{uuid}/ack` | тіло `{status: "completed" \| "failed", error?}` → `{uuid, status}` |

Списки `orders`, `stock-moves`, `clients` — `{total, limit, offset, items}`, `limit` 1–1000; період — `date_from`,
`date_to` (за замовчуванням 30 днів, не довше 366).

## API Contract (Meta → Connect)

Публічні, без ключа й JWT. Сервіс — `server/mds/services/webhooks.py`.

| Метод і шлях | Поведінка |
|---|---|
| `GET /api/webhooks/meta` | підтвердження підписки: `hub.mode=subscribe` і `hub.verify_token` = `MDS_META_VERIFY_TOKEN` → 200 з `hub.challenge` (text/plain); інакше або якщо токен не задано — 403 |
| `POST /api/webhooks/meta` | подія → `webhook_queue` зі статусом `pending`. `object`: `instagram` → `instagram`, `page` → `facebook`, інше → `unknown`. Якщо задано `MDS_META_APP_SECRET` — перевірка `X-Hub-Signature-256` (HMAC-SHA256 сирого тіла), невірний підпис — 403. Не JSON-об'єкт — 400, тіло > 1 МБ — 413. Успіх — 200 `{ok: true}` |

## Як Manager використовує webhook_queue (Phase 2b)

1. **Старт.** Manager запускається на ПК і перевіряє, що Connect доступний (`GET /api/health`).
2. **Poll.** Періодично (напр. раз на 1–5 хв) викликає `GET /api/ext/v1/webhooks?limit=50`. Отримані записи
   Connect позначає `processing` і більше їх не віддає.
3. **Обробка.** Manager розбирає `payload` (повідомлення, коментарі тощо) у себе: AI, відповіді клієнтам — з боку Manager.
4. **Ack успіху.** `POST /api/ext/v1/webhooks/{uuid}/ack` з `{"status": "completed"}` → запис `completed`.
   Повторний ack безпечний (ідемпотентний).
5. **Ack помилки.** `{"status": "failed", "error": "…"}` → `retry_count + 1`, запис знову `pending` і прийде в наступному
   poll; після 5-ї невдачі — остаточно `failed`.
6. **Збій Manager.** Якщо Manager забрав запис і не надіслав ack (вимкнули ПК), запис у `processing` довше
   `MDS_WEBHOOK_PROCESSING_TIMEOUT_MINUTES` (5) повертається в `pending` під час наступного poll.

Очищення: щогодинне завдання Connect видаляє `completed` старші 7 днів і `failed` старші 30 днів
(за `processed_at`); `pending` і `processing` не видаляються.

## Env Variables

| Змінна | Обов'язкова | Значення |
|---|---|---|
| `MDS_META_VERIFY_TOKEN` | так (для Meta) | довільний випадковий рядок 32+ символів; той самий вводиться в Meta App Dashboard |
| `MDS_META_APP_SECRET` | так (у production) | App Secret з Meta App Dashboard; без нього підпис не перевіряється і будь-хто може писати в чергу |
| `MDS_WEBHOOK_PROCESSING_TIMEOUT_MINUTES` | ні | 5 за замовчуванням |
| `MDS_MANAGER_API_KEY` | так (для Manager) | довгий випадковий рядок; без нього весь `/api/ext/v1` повертає 404 |

## Meta App Setup (для директора)

1. **Створити застосунок** на developers.facebook.com → My Apps → Create App (тип Business), прив'язати до бізнес-акаунта салону.
2. **Додати продукти:** Webhooks, Instagram (Instagram API) і/або Messenger — залежно від того, що має обробляти Manager.
3. **Webhook URL:** у налаштуваннях Webhooks вказати Callback URL `https://<домен-connect>/api/webhooks/meta`.
4. **Verify token:** у полі Verify Token ввести те саме значення, що в `MDS_META_VERIFY_TOKEN` на Railway, і натиснути
   «Verify and Save» — Meta зробить `GET` і має отримати 200. Скопіювати App Secret (Settings → Basic) у
   `MDS_META_APP_SECRET` на Railway.
5. **Підписатися** на потрібні поля (наприклад `messages`, `comments`) для сторінки / Instagram-акаунта салону.
6. **App Review:** для роботи з реальними клієнтами (не лише з тестовими користувачами) пройти перевірку Meta
   і отримати потрібні дозволи (напр. `instagram_manage_messages`, `pages_messaging`).

## Що НЕ входить у Connect v0.3.1

- Надсилання будь-чого в Meta (відповіді, повідомлення) — це робить Manager.
- AI-обробка й розбір `payload` — Manager.
- Інтерфейс черги у веб-панелі Connect — немає; стан черги видно лише через БД і API Manager.
- Прив'язка подій Meta до клієнтів чи записів Connect — немає (D-5).

## Посилання

- [CANON.md](CANON.md) — D-5, D-6a, §7 «Інтеграції».
- [API.md](API.md) — `/api/ext/v1` і `/api/webhooks`.
- [SECURITY.md](SECURITY.md) — захист ключів і вебхуків.
- [DATABASE.md](DATABASE.md) — таблиця `webhook_queue`.
- MDs Manager: [github.com/ikenlo/md-manager](https://github.com/ikenlo/md-manager).
