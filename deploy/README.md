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

## Что проверено

На 23.10 проверено:
- `docker compose config` с этим файлом собирается: наружу открыт только порт 80, API и база слушают только 127.0.0.1;
- `check.sh` проходит все шаги на моках бэкенда с настоящим агентом;
- на недоступном адресе `check.sh` падает с понятным сообщением.

Не проверено: сборка образов. В среде, где готовилась конфигурация, нет доступа к Docker Hub и npm. Первый `up --build` на сервере или ноутбуке — это и есть проверка сборки. Если упадёт, присылайте вывод.

HTTPS для демо не обязателен. Если понадобится, проще всего поставить перед `web` Caddy с автоматическим сертификатом — для этого нужен домен.
