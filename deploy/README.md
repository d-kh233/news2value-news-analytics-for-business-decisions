# deploy/

Основная инструкция по серверу — [docs/DEPLOY.md](../docs/DEPLOY.md). Запуск: `docker compose -f docker-compose.prod.yml up -d --build` из корня репозитория.

```
 интернет ──:80/:443──► caddy ──┬── /api/..., /docs, /health, /companies ... ──► api (FastAPI + агент) ──► db
                        (HTTPS)  └── всё остальное ──────────────────────────► web (интерфейс React)
                                                                worker (refresh, ask) ──┘
```

| файл | что это |
|---|---|
| `Caddyfile` | обратный прокси: какие пути идут в API, какие — в интерфейс; HTTPS при заданном `DOMAIN` |
| `web.Dockerfile` (+ `web.Dockerfile.dockerignore`) | сборка интерфейса: React → статика в nginx, с `VITE_USE_MOCKS=false` и `VITE_API_BASE_URL=/api` |
| `nginx.conf` | nginx внутри `web`: статика и маршруты React (`/analytics/...` открываются по прямой ссылке) |
| `check.sh` | быстрая проверка развёрнутого сервиса целиком: интерфейс, API, refresh агента, трейс |

Проверка с любого компьютера:
```bash
./deploy/check.sh http://<IP или домен>
```
Подробная проверка всего сценария, со входом и правами, — `backend/scripts/e2e_check.py` (docs/DEPLOY.md, шаг 6).

Интерфейс и API на одном адресе, поэтому CORS на сервере не нужен и `FRONTEND_ORIGIN` можно не задавать.

## Бесплатно на Render

Если своего сервера нет. Бесплатный тариф [Render](https://render.com/docs/free) без банковской карты: веб-сервис, PostgreSQL на 1 ГБ (живёт 30 дней) и статический сайт. Настройки — `render.yaml` в корне репозитория.

Отличия от сервера с `docker-compose.prod.yml`:
- интерфейс и API на разных адресах: `news2value-web.onrender.com` и `news2value-api.onrender.com`;
- фонового worker и консоли на бесплатном тарифе нет, поэтому API при старте сам применяет миграции, в фоне загружает демо-данные (`seed_demo.py --source synthetic`, повторно дублей не создаёт) и запускает worker в том же контейнере;
- сервис засыпает после 15 минут без запросов и просыпается примерно за минуту, вместе с ним перезапускается и загрузка демо-данных. Перед показом откройте обе ссылки заранее.

Шаги:
1. `main` со всеми изменениями (и `replay_cache/llm/`, если кэш записан) — в GitHub.
2. Сделать форк репозитория в свой аккаунт GitHub (кнопка **Fork**). Render подключает только репозитории, к которым у вас есть права администратора.
3. Войти на render.com через GitHub, разрешить доступ к форку.
4. **New → Blueprint** → форк → ветка `main` → Render прочитает `render.yaml` и спросит `DEMO_USER_PASSWORD` — пароль тестовых пользователей (не короче 8 символов, его передаём эксперту) → **Apply**.
5. Подождать сборку: API — 5–10 минут, интерфейс — 2–3. Затем открыть `https://news2value-api.onrender.com/health` — должно быть `{"status":"ok"}`. Через минуту после старта загрузятся демо-данные.
6. Открыть `https://news2value-web.onrender.com` и войти, например, `cfo.stroy@news2value.demo` с этим паролем.

Если Render выдал адреса с суффиксом (имя занято): у `news2value-api` поменять `FRONTEND_ORIGIN` на адрес интерфейса, у `news2value-web` — `VITE_API_BASE_URL` на адрес API, затем **Manual Deploy** у обоих.

Проверка целиком (со входом и правами; `check.sh` здесь не подойдёт — он без входа, а на Render вход включён):
```bash
cd backend && DEMO_USER_PASSWORD='<пароль>' uv run python scripts/e2e_check.py --base-url https://news2value-api.onrender.com
```

После обновления `main` в основном репозитории: в форке **Sync fork**, Render пересоберёт сам.

Что смотреть, если не работает: **Logs** у `news2value-api` — строки `seed_demo` (демо-данные), `worker`, ошибки миграций; у `news2value-web` — лог сборки (`npm ci`, `vite build`).
