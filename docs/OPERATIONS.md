# Встановлення та експлуатація

## 1. Де розмістити сервер

Сервер — одна програма (`python -m mds run`): API, веб-панель, Telegram-бот і бекапи.

| Варіант | Коли підходить |
|---|---|
| **Невеликий VPS** (рекомендовано), напр. 1 vCPU / 1 ГБ | Майстри відкривають застосунок з телефону звідусіль; HTTPS через Caddy |
| Комп'ютер директора в салоні (Windows) | Працює лише в мережі салону (або через Tailscale/VPN) |

Telegram-бот працює в режимі long polling — йому не потрібна публічна адреса.

## 2. Встановлення

```bash
git clone <репозиторій> && cd m-d/server
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # за потреби
python -m mds init                   # міграції БД + перший директор
```

## 3. Запуск

```bash
export MDS_TELEGRAM_TOKEN=123456:ABC...   # Windows PowerShell: $env:MDS_TELEGRAM_TOKEN="..."
python -m mds run                          # http://0.0.0.0:8000
```

Окремо: `python -m mds serve` (без бота), `python -m mds bot` (лише бот).

### Автозапуск на Linux (systemd)

```ini
# /etc/systemd/system/mds.service
[Unit]
Description=MDS Connect
After=network.target

[Service]
WorkingDirectory=/opt/m-d/server
EnvironmentFile=/opt/m-d/server/.env
ExecStart=/opt/m-d/server/.venv/bin/python -m mds run --host 127.0.0.1
Restart=always

[Install]
WantedBy=multi-user.target
```

HTTPS (Caddy, сертифікат автоматично): `/etc/caddy/Caddyfile`
```
crm.ваш-домен.com.ua {
    reverse_proxy 127.0.0.1:8000
}
```

### Автозапуск на Windows
Планувальник завдань → «При вході в систему» → дія:
`C:\mds\server\.venv\Scripts\python.exe -m mds run`, робоча папка `C:\mds\server`.

## 4. Telegram-бот

1. У @BotFather: `/newbot` → отримати токен → `MDS_TELEGRAM_TOKEN`.
2. Там же `/setcommands`:
   ```
   start - Головне меню
   book - Записатися
   my - Мої записи
   services - Послуги та ціни
   ```
3. Сповіщення персоналу: у панелі *Профіль → Отримати код* → надіслати боту `/link КОД`.
   Директор отримує всі нові записи, зміни, скасування та звіти змін; майстер — події своїх записів.
4. Записи з бота мають статус «Очікує», доки адміністратор не підтвердить (або увімкніть
   *Налаштування → Записи з Telegram одразу підтверджені*). Клієнт отримує повідомлення про підтвердження.

## 5. Встановлення застосунку на телефон і комп'ютер (PWA)

- **Android (Chrome):** відкрити адресу сервера → меню ⋮ → «Додати на головний екран / Встановити застосунок».
- **Windows (Edge/Chrome):** відкрити адресу → значок «Встановити» в адресному рядку. З'явиться в меню «Пуск».

Для встановлення PWA на телефон потрібен HTTPS (див. Caddy вище); `localhost` працює й без нього.

## 6. Резервні копії

- Автоматично: при старті та кожні `MDS_BACKUP_INTERVAL_HOURS` (24) годин у `data/backups/`,
  зберігаються останні `MDS_BACKUP_KEEP` (30). Кожна копія перевіряється `PRAGMA integrity_check`.
- Вручну: *Налаштування → Створити копію зараз* або `python -m mds backup`.
- **Важливо:** копіюйте `data/backups/` ще й за межі сервера (хмара/інший диск), напр. rclone за розкладом.
- Відновлення: зупинити сервер → скопіювати потрібний `mds-YYYYMMDD-HHMMSS.db` як `data/mds.db` → запустити.
- PostgreSQL: замість вбудованих бекапів — `pg_dump` за розкладом.

Також зберігайте `data/secret.key` (без нього всі користувачі просто увійдуть заново — дані не втрачаються).

## 7. Оновлення

```bash
git pull
pip install -r requirements.txt
python -m mds migrate      # застосувати нові міграції (до запуску!)
# перезапустити сервіс
```
Перед оновленням зробіть резервну копію.
