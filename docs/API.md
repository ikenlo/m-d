# REST API

Інтерактивна документація з прикладами: `http://<сервер>:8000/docs` (Swagger) — генерується автоматично.

Авторизація: `POST /api/auth/login` → `token`; далі заголовок `Authorization: Bearer <token>`.
Гроші — цілі копійки, відсотки — bp (5000 = 50%). Дати-час — ISO, локальний час салону.
Помилки: `{"error": "<код>", "detail": "<повідомлення українською>"}`; 401/403/404/409/422.

| Метод і шлях | Хто | Призначення |
|---|---|---|
| `POST /api/auth/login` | усі | Вхід |
| `GET /api/auth/me` | усі | Поточний користувач (+ `open_shift_id` для майстра) |
| `POST /api/auth/change-password` | усі | Зміна власного пароля |
| `POST /api/auth/telegram-code` | усі | Код для `/link` у боті |
| `GET/POST /api/users`, `PATCH /api/users/{id}` | директор | Персонал, відсоток, режим матеріалів, графік, послуги |
| `POST /api/users/{id}/reset-password` | директор | Скидання пароля |
| `GET/POST /api/services`, `PATCH /api/services/{id}` | читання — усі; зміна — директор | Послуги |
| `GET/POST /api/materials`, `PATCH /api/materials/{id}` | те саме | Матеріали |
| `GET /api/clients`, `GET /api/clients/{id}` | персонал (майстер — лише свої клієнти) | Клієнти та історія відвідувань |
| `POST /api/clients`, `PATCH /api/clients/{id}` | директор, адміністратор | Додати / змінити клієнта (із записом у журнал) |
| `GET /api/appointments?date_from&date_to&master_id` | персонал (майстер — лише свої) | Календар |
| `GET /api/availability?master_id&service_id&day` | персонал | Вільні слоти |
| `POST /api/appointments`, `PATCH /api/appointments/{id}` | персонал | Створення / перенесення / зміна |
| `POST /api/appointments/{id}/status` | персонал | `confirmed`, `in_progress`, `cancelled`, `no_show` |
| `POST /api/appointments/{id}/close` | майстер (свої), адмін, директор | **Закрити замовлення** |
| `GET /api/orders`, `GET /api/orders/{id}` | директор; майстер — свої | Замовлення |
| `PATCH /api/orders/{id}` | директор | Виправлення з причиною |
| `GET /api/shifts/current`, `POST /api/shifts/open`, `POST /api/shifts/close` | майстер | Зміна |
| `GET /api/shifts`, `GET /api/shifts/{id}` | директор; майстер — свої | Звіти змін |
| `GET/POST /api/payouts` | директор; майстер — перегляд своїх | Виплати |
| `GET /api/finance/dashboard?period=day\|week\|month\|year` або `date_from&date_to` | директор | Панель + розрізи по майстрах/послугах/днях |
| `GET /api/finance/my` | майстер | Заробіток за день/тиждень/місяць і баланс |
| `GET/POST /api/expenses`, `PATCH/DELETE /api/expenses/{id}` | директор | Витрати |
| `GET/PUT /api/settings` | читання — усі; зміна — директор | Налаштування |
| `GET /api/journal/ledger`, `GET /api/journal/audit` | директор | Журнали |
| `GET/POST /api/backups` | директор | Резервні копії |
| `GET /api/gdrive`, `POST /api/gdrive/connect`, `POST /api/gdrive/backup-now`, `POST /api/gdrive/disconnect` | директор | Google Drive: стан, підключення (OAuth), копія зараз, відключення |
| `GET /api/gdrive/callback` | — (підписаний `state`) | Повернення з Google після підключення |
| `GET /api/finance/insights?period=…` | директор | Аналітика: порівняння з попереднім періодом, прогноз місяця, середній чек по майстрах, нові/повторні клієнти, дні тижня |
| `GET /api/journal/feed?date_from&date_to&category&q` | директор | Журнал простими словами (стрічка подій, фільтр і пошук) |
| `GET/POST /api/utilities/readings`, `PATCH /api/utilities/readings/{id}`, `POST …/{id}/confirm` | директор | Показники лічильника, тариф, оплата, підтвердження (витрата «Комунальні») |
| `GET /api/generator`, `POST /api/generator/start`, `POST /api/generator/stop` | персонал | Стан і перемикач генератора (прогноз — лише директору) |
| `GET/POST /api/generator/runs`, `PATCH /api/generator/runs/{id}`, `POST /api/generator/motohours` | директор (перегляд — і адміністратор) | Запуски, ручне додавання/коригування, звірка мотогодин |
| `GET /api/utilities/summary`, `GET /api/utilities/allocation`, `POST /api/utilities/allocate`, `POST /api/utilities/reverse` | директор | Аналітика місяця, розподіл між майстрами, скасування (сторно) |
| `GET /api/utilities/charges` | директор; майстер — свої | Списання з майстрів |
| `GET /api/health` | — | Перевірка роботи; `version` — розгорнута версія |

## Звіт для податкової (директор)

- `GET /api/reports/income?year=&quarter=` — дохід по місяцях/кварталах, готівка/картка/переказ, з початку року, ліміт (`fop_income_limit` у налаштуваннях, копійки).
- `GET /api/reports/income.csv?year=&quarter=&detail=day|order` — CSV для Excel (UTF-8 з BOM, «;», суми з комою).
- Дохід = сума закритих замовлень на дату закриття (після виправлень — остання сума). Нічого не подає в ДПС.
- `GET /api/reports/declaration?year=` — цифри декларації ФОП 1 групи і чого бракує в даних ФОП;
  `GET /api/reports/declaration.zip?year=` — ZIP з двома XML (річна F0103407 + Додаток 1 F0134107) для Електронного кабінету.

## Податки і зарплата (директор) — docs/PAYROLL.md
- Ставки: `GET/POST /api/taxes/rates`; податки ФОП: `GET /api/taxes/fop?year=`, `POST /api/taxes/fop/accrue`,
  `POST /api/taxes/fop/{id}/correct`, `POST /api/taxes/payments`.
- Працівники: `GET /api/employees`, `PUT /api/employees/{user_id}`.
- Зарплата: `GET /api/payroll?month=`, `GET /api/payroll/calc?gross=&month=&user_id=`, `POST /api/payroll`,
  `POST /api/payroll/{id}/correct`, `POST /api/payroll/payments`, `GET /api/payroll/history/{user_id}`,
  `GET /api/payroll/quarter?year=&quarter=`, `GET /api/payroll/quarter.csv?year=&quarter=`.

## Для бухгалтера (директор)
- `GET /api/reports/accountant?year=&quarter=` — дохід (каса + банк) по місяцях/кварталах, реквізити ФОП, ліміт, працівники.
- `GET /api/reports/accountant.xlsx?year=&quarter=` — пакет Excel для бухгалтера.
