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
