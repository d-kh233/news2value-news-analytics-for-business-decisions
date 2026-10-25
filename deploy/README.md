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
