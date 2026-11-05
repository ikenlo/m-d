# Інтеграція з MDs manager (AI-адміністратор і маркетинг)

MDS Connect — **джерело правди** для записів, клієнтів, графіка й грошей.
MDs manager (окремий сервіс, репозиторій `ikenlo/md-managment`) — AI-адміністратор у Telegram/Instagram
і маркетинговий модуль. Він нічого не зберігає про записи сам: читає й пише їх тут, через `/api/ext/v1`.

## Доступ

- Вмикається змінною `MDS_INTEGRATION_TOKEN` (довгий випадковий рядок, `openssl rand -hex 32`).
  Без неї всі `/api/ext/v1/*` відповідають 404.
- Кожен запит — заголовок `X-MDS-Integration-Token: <токен>`. JWT персоналу тут не діє.
- Той самий токен кладеться в MDs manager як `MDS_TOKEN`.

## Що передає MDs manager → MDS

| Метод | Шлях | Що робить |
|---|---|---|
| GET | `/api/ext/v1/catalog` | Активні послуги (ціни в копійках) і майстри |
| GET | `/api/ext/v1/slots?service_id&date_from&date_to[&master_id]` | Вільний час для клієнтів (≤31 день) |
| POST | `/api/ext/v1/appointments` | Запис від імені клієнта + атрибуція |
| POST | `/api/ext/v1/appointments/lookup` | Майбутні записи клієнта (телефон / Telegram ID / Instagram ID) |
| POST | `/api/ext/v1/appointments/{id}/cancel` | Скасування — лише власного запису клієнта |

Запис іде через `bookings.create_appointment` з actor роллю `client` (`Actor.via(channel)`), тож діють
антиспам, графік, мінімальний запас часу і захист від подвійних записів. `appointments.source` =
`telegram-ai` або `instagram`. Повтор з тим самим `external_ref` повертає той самий запис (ідемпотентність).

Атрибуція зберігається в `appointment_attribution`: `channel`, `lead_source` (мітка посилання, напр.
`ig_reels_1201`), `utm_*`, `campaign_id`. Перше джерело клієнта (`clients.source`) заповнюється, лише якщо
порожнє. Кожна зміна пише `audit`.

## Що отримує MDs manager ← MDS (для окупності реклами)

| Метод | Шлях | Що повертає |
|---|---|---|
| GET | `/api/ext/v1/visits?date_from&date_to` | Записи за датою візиту: статус (у т.ч. скасування й неявки), ціна, фактична оплата, частка салону, атрибуція |
| GET | `/api/ext/v1/ad-spend?date_from&date_to` | Витрати категорії «Реклама» |

## Не робимо

- Персональних даних більше, ніж потрібно для запису (ім'я, телефон, ID месенджера).
- Жодних змін грошей, замовлень, змін і виплат через інтеграцію — лише читання.
