# Експлуатація: запуск, змінні, бекапи, оновлення, застосунки

Для розробника й агента. Безпека — [SECURITY.md](SECURITY.md); бот — [BOT.md](BOT.md).

## 1. Запуск

```bash
cd server
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt                       # для розробки: requirements-dev.txt
python -m mds init          # міграції + перший директор (питає логін/пароль)
python -m mds run           # API + панель + бот + бекапи, http://0.0.0.0:8000
```
Інші команди: `serve` (без бота), `bot` (лише бот), `start` (продакшен: міграції → директор із змінних →
прайс → сервер із ботом), `migrate`, `backup`, `create-user`, `wipe-data`, `demo-fop1` (D-8).

Сервер — **один процес** (API, бот, фонові цикли: бекап кожні N год, нагадування й черги кожні 5 хв).
Запускати лише **одну** репліку.

## 2. Змінні середовища

| Змінна | За замовчуванням | Призначення |
|---|---|---|
| `MDS_DATA_DIR` | `RAILWAY_VOLUME_MOUNT_PATH` або `./data` | БД, бекапи, `secret.key`, `gdrive.json` |
| `MDS_DATABASE_URL` | `sqlite:///<data>/mds.db` | БД |
| `MDS_SECRET_KEY` | файл `data/secret.key` | підпис JWT — у продакшені задати змінною |
| `MDS_TOKEN_TTL_HOURS` | 12 | строк дії сесії (**[0.3.0]** + автовихід 30 хв) |
| `MDS_TIMEZONE` | `Europe/Kyiv` | час салону |
| `MDS_HOST`, `MDS_PORT` / `PORT` | `0.0.0.0`, 8000 | адреса |
| `MDS_TELEGRAM_TOKEN` | — | бот (без нього бот не стартує) |
| `MDS_DIRECTOR_USERNAME`, `MDS_DIRECTOR_PASSWORD`, `MDS_DIRECTOR_NAME` | — | перший директор (`start`), пароль після входу видалити |
| `MDS_BACKUP_INTERVAL_HOURS`, `MDS_BACKUP_KEEP`, `MDS_BACKUP_DIR` | 6, 40, `data/backups` | локальні бекапи |
| `MDS_GDRIVE_CLIENT_ID`, `MDS_GDRIVE_CLIENT_SECRET`, `MDS_GDRIVE_KEEP` | —, —, 180 | Google Drive (OAuth); **[0.3.0+]** `MDS_GDRIVE_SA_JSON`, `MDS_GDRIVE_FOLDER_ID` (Q-7) |
| `MDS_TWOFA_DISABLED` | — | аварійно вимкнути 2FA |
| `MDS_PRIVAT_TERMINAL_CLID`, `_SECRET`, `_CLIENT_TOKEN` | — | «Термінал» (режим `live`; **[0.3.0]** не використовується, D-6c) |
| `MDS_TURBOSMS_TOKEN`, `MDS_TURBOSMS_SENDER` | — | **[0.3.0−]** SMS із Connect вимикаються (D-6a) |
| `MDS_INTEGRATION_TOKEN` | — | ключ `/api/ext/v1` для MDs Manager (гілка ext) |
| **[0.3.0+]** `MDS_ENV` | `production` | `production` / `test`; у `production` заборонені `MDS_WIPE_DATA`, `MDS_DEMO_FOP1`, `wipe-data`, `demo-fop1`, mock-оплати |
| `MDS_WIPE_DATA`, `MDS_DEMO_FOP1` | — | лише тестове середовище (§7) |

Файл `.env.example` зі списком — **[0.3.0+]**.

## 3. Railway (поточний хостинг; тимчасовий — канон: VPS)

- Сервіс з GitHub `ikenlo/m-d`, гілка `main`; збірка — `Dockerfile` (`CMD python -m mds start`) і `railway.json`.
- **Volume** змонтований у `/data` (інакше база зникне при оновленні). **Replicas = 1.**
- Змінні — у Variables (§2). Домен — Settings → Networking (HTTPS автоматично).
- Оновлення: PR → CI зелений → людина натискає Merge → Railway сам збирає й перезапускає (1–3 хв, бот у цей час
  недоступний). Міграції застосовуються при старті (`start`).
- Журнали: Deployments → View Logs. «Директора ще немає» — не задано `MDS_DIRECTOR_*`.

### Власний сервер (VPS)

systemd: `ExecStart=/opt/m-d/server/.venv/bin/python -m mds start --host 127.0.0.1`, `EnvironmentFile=…/.env`,
`Restart=always`. HTTPS — Caddy: `crm.домен { reverse_proxy 127.0.0.1:8000 }`. Бот працює через long polling —
публічна адреса йому не потрібна. PWA на телефоні вимагає HTTPS.

## 4. Бекапи

- Автоматично: при старті і кожні `MDS_BACKUP_INTERVAL_HOURS` — `data/backups/mds-YYYYMMDD-HHMMSS.db`
  (SQLite backup API), кожна перевіряється `PRAGMA integrity_check`, лишаються останні `MDS_BACKUP_KEEP`.
- Вручну: «Налаштування → Створити копію зараз / Зберегти на диск зараз» або `python -m mds backup`.
- PostgreSQL — `pg_dump` за розкладом (вбудовані бекапи лише для SQLite).
- **[0.3.0+]** (CANON §6): 30 днів за віком; шифрування перед завантаженням; третя копія на ПК (кнопка
  «Завантажити копію»); помилка локального бекапу → Telegram обом директорам; Service Account.

### Google Drive

Зараз — OAuth від імені директора (scope `drive.file`, доступ у `data/gdrive.json`, лише на сервері).
1. console.cloud.google.com → проєкт → увімкнути **Google Drive API**.
2. OAuth consent screen: External, scope `.../auth/drive.file`, **Publish app** (інакше доступ відкликається через 7 днів).
3. Clients → Web application; Redirect URI `https://<домен>/api/gdrive/callback`.
4. `MDS_GDRIVE_CLIENT_ID`, `MDS_GDRIVE_CLIENT_SECRET` у змінні сервера.
5. Директор: «Налаштування → Резервні копії → Підключити Google Drive».
Копії йдуть у папку «MDS Connect — резервні копії», зберігаються останні `MDS_GDRIVE_KEEP`.
Помилка завантаження → повідомлення директорам (не частіше разу на добу).
**[0.3.0+]** Service Account (Q-7): у SA немає власного місця на звичайному Google-акаунті — потрібен Shared Drive
(Workspace) або папка користувача Workspace, інакше `storageQuotaExceeded`.

## 5. Відновлення

1. Зупинити сервіс.
2. Скопіювати потрібний `mds-YYYYMMDD-HHMMSS.db` як `data/mds.db` (з диска сервера або з Google Drive).
3. Запустити: міграції доведуть схему до поточної версії.
4. Перевірити: `GET /api/health`, вхід директора, останні замовлення.
`data/secret.key` не обов'язковий: без нього всі просто увійдуть заново.
**[0.3.0+]** скрипт `scripts/test_restore.sh` (відновлення в тимчасову теку + `integrity_check` + підрахунок рядків)
і перевірка в CI.

## 6. Оновлення і міграції

```bash
git pull && pip install -r requirements.txt
python -m mds migrate      # до запуску; зробіть бекап перед цим
```
Нова міграція: змінити `models.py` → `alembic revision --autogenerate -m "опис"` (з `server/`) → перевірити файл
(у SQLite — `batch_alter_table`) → тест `test_migrations_match_models`.

## 7. Тестове середовище: демо і очищення (D-8)

- `MDS_DEMO_FOP1=yes` або `python -m mds demo-fop1` — бекап, видалення лише демо-даних (`demo_*`, «(демо)»,
  телефони +38067999xxxx), сценарій «ФОП 1 група». Після заповнення змінну видалити.
- `MDS_WIPE_DATA=<мітка>` або `python -m mds wipe-data --yes` — бекап, потім видалення записів, замовлень,
  клієнтів, змін, виплат, витрат, складу, податків, журналів. Лишаються акаунти, послуги, налаштування.
  З тією самою міткою не повторюється.
- **[0.3.0]** обидва — помилка, якщо `MDS_ENV=production` (або не задано).

## 8. Застосунки Android і Windows

Оболонки над веб-панеллю (`apps/android` — WebView без бібліотек, мінімум Android 7; `apps/windows` — Electron,
Windows 10/11 x64). Логіки немає — оновлення сайту видно одразу. Перевипуск лише при зміні оболонки.
- Версія: `versionCode/versionName` в `apps/android/app/build.gradle`, `version` в `apps/windows/package.json`,
  назви файлів у `.github/workflows/apps.yml`.
- PR → workflow «Apps» збирає APK (і запускає на емуляторі) та EXE. Реліз: Actions → Apps → Run workflow → `release`.
- Підпис Android: секрети GitHub `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD` (ключ `mds`). Копію ключа
  зберігати окремо; без секретів CI підписує тестовим ключем. `.jks` ніколи не комітити.
- Локально: `cd apps/android && gradle assembleRelease [-PappUrl=…]`; `cd apps/windows && npm ci && npm start`
  (`MDS_APP_URL=http://localhost:8000`), `npm run dist`.

## 9. Версії і релізи

- Версія — `server/mds/__init__.py::__version__` і `server/pyproject.toml::version` + розділ
  `## [X.Y.Z] — дата` першим у `CHANGELOG.md` (корінь репозиторію; тест `test_version.py`).
- Після злиття `.github/workflows/release.yml` створює тег `vX.Y.Z` і GitHub Release з описом із CHANGELOG.
- Наступний реліз — **0.3.0** (канон, D-1).

## 10. Моніторинг

- `GET /api/health` → стан і версія.
- «Налаштування → Швидкодія» (`/api/perf`): медіана і p95 запитів API і сторінок у браузерах (у пам'яті,
  скидається при деплої); запити ≥ 1 с — `WARNING повільний запит` у журналі.
- Заголовок `Server-Timing` у кожній відповіді API.
