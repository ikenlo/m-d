# MDS Connect

Внутрішня система салону M&D: записи клієнтів, закриття замовлень, зміни й виплати майстрам, склад,
фінанси директора, Telegram-бот. Лише для одного салону — не SaaS.

```bash
cd server && pip install -r requirements-dev.txt
python -m mds init && python -m mds run      # http://localhost:8000
python -m pytest
```

| Документ | Про що |
|---|---|
| [CLAUDE.md](CLAUDE.md) | правила розробки (з чого починати) |
| [docs/CANON.md](docs/CANON.md) | затверджені бізнес-правила і рішення директора |
| [docs/FINANCE.md](docs/FINANCE.md) | фінансова модель |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | будова системи |
| [docs/API.md](docs/API.md) · [docs/DATABASE.md](docs/DATABASE.md) · [docs/BOT.md](docs/BOT.md) | довідники |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) · [docs/SECURITY.md](docs/SECURITY.md) | запуск, бекапи, безпека |
| [docs/ROADMAP.md](docs/ROADMAP.md) · [CHANGELOG.md](CHANGELOG.md) | план і історія версій |

## Webhook Queue (v0.3.1)

Вебхуки Meta (Instagram, Facebook) приймає Connect на публічному `/api/webhooks/meta` і лише кладе їх у чергу
`webhook_queue`. Обробляє їх MDs Manager. Правила — [CANON.md](docs/CANON.md) D-5, D-6a і §7; ендпоінти —
[API.md](docs/API.md); повний контекст — [docs/CONTEXT_MANAGER_INTEGRATION.md](docs/CONTEXT_MANAGER_INTEGRATION.md).

**1. Змінні на Railway (Variables) — обов'язково для production**

| Змінна | Значення |
|---|---|
| `MDS_META_VERIFY_TOKEN` | випадковий рядок 32+ символів, напр. `python -c "import secrets;print(secrets.token_urlsafe(32))"`; потрібен для перевірки підписки Meta |
| `MDS_META_APP_SECRET` | App Secret з Meta App Dashboard (Settings → Basic); потрібен для перевірки підпису |
| `MDS_WEBHOOK_PROCESSING_TIMEOUT_MINUTES` | необов'язково, `5` за замовчуванням |
| `MDS_MANAGER_API_KEY` | ключ, яким MDs Manager читає `/api/ext/v1` (без нього — 404) |

**2. Увага.** Якщо `MDS_META_APP_SECRET` не задано, підпис вебхуків не перевіряється. У production це небезпечно:
будь-хто може писати в чергу. Завжди задавайте на Railway обидві змінні — `MDS_META_VERIFY_TOKEN` і
`MDS_META_APP_SECRET` ([SECURITY.md](docs/SECURITY.md)).

**3. Перевірка після деплою**

```bash
curl "https://<your-connect-url>/api/webhooks/meta?hub.mode=subscribe&hub.challenge=test&hub.verify_token=<MDS_META_VERIFY_TOKEN>"
```

Очікується `test` (text/plain, 200 OK). Невірний токен — 403 `Forbidden`.

**4. Meta App** — покрокове налаштування: [docs/CONTEXT_MANAGER_INTEGRATION.md](docs/CONTEXT_MANAGER_INTEGRATION.md),
розділ «Meta App Setup (для директора)».

**5. Сторона Manager.** Manager забирає вебхуки `GET /api/ext/v1/webhooks` і підтверджує обробку
`POST /api/ext/v1/webhooks/{uuid}/ack` (заголовок `X-API-Key`). Налаштування Manager —
[github.com/ikenlo/md-manager](https://github.com/ikenlo/md-manager).
