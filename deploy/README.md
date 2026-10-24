# Деплой

Весь сервис поднимается на любом сервере с Docker одной командой. Интерфейс, API и документация API
работают на одном адресе. Ключ модели на сервере не нужен: агент берёт ответы из записанного кэша
(`LLM_MODE=replay`).

```
           :80
 браузер ──────► web (nginx) ──── /api/... ───► api (FastAPI + агент) ───► db (PostgreSQL)
                 статика React     /docs           127.0.0.1:8000             127.0.0.1:5432
```

| файл | что это |
|---|---|
| `deploy/docker-compose.prod.yml` | добавляет к `docker-compose.yml` фронтенд на порту 80 |
| `deploy/web.Dockerfile` | сборка React → статика в nginx |
| `deploy/nginx.conf` | `/api/...` → API, `/docs` → документация, остальное — интерфейс |
| `deploy/check.sh` | проверка развёрнутого сервиса целиком |

## Что нужно заранее

1. **Одна ветка со всем кодом.** Деплоим `main` после того, как в неё влиты `feat/agent` и `feat/frontend`. Ветки сливаются без конфликтов, это проверено 23.10. Если `main` ещё не собран, можно деплоить временную ветку, где они слиты.
2. **Сервер:** Ubuntu 22.04 или 24.04, от 2 ГБ памяти, открытый порт 80. Адрес должен открываться из России без VPN — так смотрит жюри.
3. **Записанный кэш ответов модели** в `replay_cache/llm/`. Как его записать — в корневом README, раздел «С моделью и запись кэша для жюри». Без кэша сервис тоже работает, но карточки будут по шаблонам.

## Первый запуск

На сервере:

```bash
# Docker
curl -fsSL https://get.docker.com | sh

# код
git clone https://github.com/d-kh233/news2value-news-analytics-for-business-decisions news2value
cd news2value
git checkout main

# настройки: из шаблона, затем поменять пароль базы и режим модели
cp .env.example .env
nano .env        # POSTGRES_PASSWORD и тот же пароль в DATABASE_URL; LLM_MODE=replay;
                 # AGENT_TODAY — дата, с которой записан кэш

# запуск
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build

# проверка
./deploy/check.sh
```

Если всё в порядке, `check.sh` пишет «Готово». Интерфейс открывается по `http://<адрес сервера>/`, документация API — по `http://<адрес сервера>/docs`.

Данные кейса, если нужны в базе, копируются на сервер отдельно — в git их нет:
```bash
scp df_*.csv user@сервер:~/news2value/data/raw/
docker compose exec api python scripts/load_csv.py
```

## Обновление

```bash
cd ~/news2value
git pull
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
./deploy/check.sh
```

## Если что-то не так

| что видно | что смотреть |
|---|---|
| `check.sh`: «интерфейс не отдаётся» | `docker compose logs web` — обычно сборка фронта упала на `npm ci` (нет доступа к npm) |
| интерфейс открывается, данные не грузятся (502) | `docker compose logs api` — API не стартовал, часто из-за пароля базы: в `POSTGRES_PASSWORD` и `DATABASE_URL` он должен совпадать |
| «задача агента: failed» | текст ошибки в `GET /api/jobs/<id>`, подробности — в `docker compose logs api` |
| все карточки «по шаблонам» | нет записанного кэша для этой компании и роли, либо `AGENT_TODAY` не совпадает с датой записи |
| порт 80 занят | в `.env` задать `WEB_PORT=8080` |

Ярлык для команд:
```bash
alias dc='docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml'
dc ps; dc logs -f api; dc restart api; dc down
```

## Бесплатно на Render

Если своего сервера нет. Бесплатный тариф Render без банковской карты: 512 МБ памяти, сервис засыпает после 15 минут без запросов и просыпается примерно за минуту. Настройки — в `render.yaml` в корне репозитория.

Отличия от запуска на своём сервере:
- нет базы — API работает на моках (`c-001`…), миграции не запускаются;
- нет nginx — интерфейс и API на разных адресах, интерфейс ходит в API напрямую (`VITE_API_BASE_URL`), API разрешает этот адрес в `CORS_ORIGINS`.

Шаги:
1. Собрать `main` (см. «Что нужно заранее», п. 1) и запушить. Render берёт код из GitHub, `render.yaml` должен быть в той же ветке.
2. Войти на render.com через GitHub, дать доступ к репозиторию.
3. **New → Blueprint** → репозиторий → ветка `main` → **Apply**. Render создаст два сервиса: `news2value-api` и `news2value-web`.
4. Подождать сборку (API — 5–10 минут), открыть `https://news2value-api.onrender.com/health` — должно быть `{"status":"ok"}`.
5. Открыть `https://news2value-web.onrender.com` — это ссылка для жюри.

Если Render выдал адреса с суффиксом (имя занято): в сервисе `news2value-web` поменять `VITE_API_BASE_URL` на адрес API, в `news2value-api` — `CORS_ORIGINS` на `["<адрес интерфейса>"]`, затем **Manual Deploy** у обоих.

Проверка целиком — тем же скриптом, API указывается отдельно:
```bash
API=https://news2value-api.onrender.com ./deploy/check.sh https://news2value-web.onrender.com
```

Карточки от модели будут, только если в `main` закоммичен `replay_cache/llm/`, записанный с теми же `AGENT_TODAY`, `LLM_USE_SMART`, `LLM_USE_CRITIC`, что в `render.yaml` (корневой README, «С моделью и запись кэша для жюри»).

Перед показом жюри откройте обе ссылки за пару минут — сервисы проснутся.

## Что проверено

На 23.10 проверено:
- `docker compose config` с этим файлом собирается: наружу открыт только порт 80, API и база слушают только 127.0.0.1;
- `check.sh` проходит все шаги на моках бэкенда с настоящим агентом;
- на недоступном адресе `check.sh` падает с понятным сообщением.

Не проверено: сборка образов. В среде, где готовилась конфигурация, нет доступа к Docker Hub и npm. Первый `up --build` на сервере или ноутбуке — это и есть проверка сборки. Если упадёт, присылайте вывод.

HTTPS для демо не обязателен. Если понадобится, проще всего поставить перед `web` Caddy с автоматическим сертификатом — для этого нужен домен.
