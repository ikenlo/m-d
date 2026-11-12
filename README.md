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

## Webhook Queue

Вебхуки Meta (Instagram, Facebook) приймає Connect на публічному `/api/webhooks/meta` і лише кладе їх у чергу
`webhook_queue`. Обробляє їх MDs Manager: забирає `GET /api/ext/v1/webhooks` і підтверджує
`POST /api/ext/v1/webhooks/{uuid}/ack` (ключ `X-API-Key`). Правила — [CANON.md](docs/CANON.md) D-5, D-6a і §7;
ендпоінти — [API.md](docs/API.md); змінні `MDS_META_*` — `server/.env.example`.
