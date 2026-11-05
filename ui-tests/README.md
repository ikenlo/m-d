# UI-регресія (Playwright)

`smoke.js` обходить усі сторінки директора й майстра (з `NAV[роль]`) у трьох режимах:
телефон 390×844 (світла й темна тема) і комп'ютер 1400×900. Перевіряє:

- немає помилок JavaScript і `console.error`;
- немає відповідей API 5xx;
- сторінка завантажилась (зник скелет) за 15 с і не показує помилку замість вмісту;
- немає горизонтальної прокрутки всієї сторінки;
- кнопка «+» на телефоні рівно по центру.

Знімки всіх сторінок — у `ui-tests/out/` (у CI — артефакт `ui-screenshots`, 7 днів).

Локально:

```bash
MDS_DATA_DIR=/tmp/mds-ui ./ui-tests/seed.sh            # демо-база: margaryta / dmitry + рік даних
cd server && MDS_DATA_DIR=/tmp/mds-ui MDS_PORT=8770 python -m mds serve &
cd ui-tests && npm install --no-save playwright@1.48.2 && npx playwright install chromium
node smoke.js http://localhost:8770
```

Нова сторінка (`VIEWS.<name>` + пункт у `NAV`) потрапляє в перевірку автоматично.
