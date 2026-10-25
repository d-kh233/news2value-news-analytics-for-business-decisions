# Деплой News2Value на сервер

Инструкция для человека, который делает это впервые. Команды выполняются **на сервере** в терминале (через SSH). Подключаться к серверу вы будете сами, скрипты в репозитории этого не делают.

В итоге на сервере работают пять контейнеров (изолированные программы Docker):

```
 интернет ──:80/:443──► caddy ──┬──► api (FastAPI + агент) ──► db (PostgreSQL)
                        (HTTPS)  │        ▲
                                 │        └── worker (фоновые задачи refresh и ask)
                                 └──► web (интерфейс React)
```

Наружу открыт только Caddy (обратный прокси, принимает запросы и передаёт их дальше). Интерфейс и API работают на одном адресе: `http://<адрес>/` — интерфейс, `/api/...` и `/docs` — API. Базу из интернета не видно.

Файлы: [docker-compose.prod.yml](../docker-compose.prod.yml) (что запускать), [deploy/Caddyfile](../deploy/Caddyfile) (настройки Caddy), `.env` (настройки и секреты, создаёте на сервере сами).

> Что в какой файл в `deploy/` — в [deploy/README.md](../deploy/README.md).

## 0. Что нужно заранее

- Сервер с Ubuntu 22.04 или 24.04, не меньше 2 ГБ памяти и 10 ГБ диска.
- Открытые порты 80 и 443. Обычно они открываются в панели хостинга, в разделе «Firewall» или «Сетевые правила».
- Для HTTPS — домен (например, `app.example.ru`), у которого A-запись указывает на IP сервера. Без домена всё работает по `http://<IP>`, но пароли тогда передаются без шифрования (см. [SECURITY.md](SECURITY.md)).

## 1. Установить Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER     # чтобы запускать docker без sudo
exit                              # выйти и зайти по SSH заново, иначе группа не применится
docker --version                  # должна напечататься версия
docker compose version
```

## 2. Скачать код

```bash
git clone https://github.com/d-kh233/news2value-news-analytics-for-business-decisions news2value
cd news2value
git checkout <тег или ветка сдачи>     # например: git checkout v1.0
```

## 3. Создать `.env`

```bash
cp .env.example .env
nano .env          # сохранить: Ctrl+O, Enter; выйти: Ctrl+X
```

Что поменять обязательно:

| переменная | что поставить |
|---|---|
| `POSTGRES_PASSWORD` | свой пароль базы (буквы и цифры, без `@ : / %`) |
| `DATABASE_URL` | тот же пароль: `postgresql://news2value:<пароль>@db:5432/news2value` |
| `JWT_SECRET` | случайная строка. Получить её: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `DEMO_USER_PASSWORD` | пароль тестовых пользователей, не короче 8 символов. Его вы передадите эксперту |
| `AUTH_ENABLED` | `true`, чтобы без входа данные не открывались |
| `DOMAIN` | домен (`app.example.ru`) или ничего — тогда работа по IP |

Уже стоят в шаблоне и для демо не меняются: `NEWS_SOURCE=db`, `LLM_MODE=replay`, `LLM_USE_SMART=1`, `AGENT_TODAY=2026-10-25` — с ними записаны ответы модели в `replay_cache/` (корневой README, «Модель и запись ответов для жюри»). `FRONTEND_ORIGIN` не нужен: интерфейс на том же адресе.

`.env` никуда не отправляйте и в git не добавляйте: в нём пароли.

## 4. Запустить

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

Первая сборка занимает 5–8 минут (API и интерфейс). Через минуту у `api`, `db` и `worker` в колонке STATUS должно быть `healthy`, у `web` — `Up`, у `caddy` — `Up` или `healthy`.

Чтобы не набирать длинную команду, заведите сокращение (действует до выхода из SSH):
```bash
alias dc='docker compose -f docker-compose.prod.yml'
```
Дальше в инструкции используется `dc`.

## 5. Заполнить базу демо-данными

```bash
dc exec api python scripts/seed_demo.py --source synthetic
```

Команда загружает 30 синтетических компаний, 13 демо-новостей, демо-профили, тестовых пользователей и инсайты для трёх демо-компаний. Интернет для этого не нужен. В конце должно быть три строки `инсайты есть`.

Если на сервер скопированы CSV кейса (`data/raw/`, в git их нет), вместо этого: `dc exec api python scripts/seed_demo.py --source raw`.

## 6. Проверить с любого компьютера

На своём компьютере, в папке репозитория:
```bash
cd backend
DEMO_USER_PASSWORD='<пароль из .env сервера>' uv run python scripts/e2e_check.py --base-url https://app.example.ru
# без домена: --base-url http://<IP сервера>
```
Все строки должны быть `[OK]`, в конце — `Итог: всё OK`. Если какая-то строка `[FAIL]`, причина написана в ней же.

Интерфейс вместе с API: `./deploy/check.sh https://app.example.ru` (из корня репозитория).

Адреса: интерфейс — `https://app.example.ru/`, документация API — `https://app.example.ru/docs`.

## 7. Обновить версию

```bash
cd ~/news2value
git fetch --tags
git checkout <новый тег или ветка>     # или git pull, если вы на ветке
dc up -d --build                        # пересобрать и перезапустить; миграции базы применятся сами
dc ps
```
Данные базы при этом не теряются: они лежат в томе `pgdata`.

## 8. Логи (что происходит внутри)

```bash
dc logs -f api          # запросы и ошибки API; выход — Ctrl+C
dc logs -f worker       # фоновые задачи: refresh, ask
dc logs --tail 100 caddy
dc logs --since 30m     # всё за последние 30 минут
```
Пароли, токены и email в логах заменены на `***`.

## 9. Если что-то сломалось: быстрый откат

**Уровень 1 — сломался агент или модель** (задачи `failed`, карточки не появляются). Включите заглушку: карточки будут строиться по совпадению слов профиля с новостью, без `agent/` и `llm/`.
```bash
nano .env               # ML_MODE=fake
dc up -d                # перезапустит api и worker с новой настройкой
```
После этого `e2e_check.py` должен проходить.

**Уровень 2 — не работают инсайты из базы.** Лента, дайджест и вопросы будут из встроенных моков (выдуманные данные, как на ранних этапах):
```bash
nano .env               # USE_MOCKS=true и ML_MODE=fake
dc up -d
```
В этом режиме данные есть только у моковых компаний `c-001`, `c-002`, `c-003`: `curl http://<адрес>/companies/c-001/insights`. `e2e_check.py` тут не подходит — он проверяет демо-компании из базы.

Вернуть как было — те же строки обратно (`ML_MODE=real`, `USE_MOCKS=false`) и снова `dc up -d`.

**Откатить код** на прошлую версию: `git checkout <прошлый тег>` и `dc up -d --build`.

## Частые проблемы

| что видно | что делать |
|---|---|
| `api` не становится `healthy` | `dc logs api`. Чаще всего пароль в `POSTGRES_PASSWORD` и `DATABASE_URL` не совпадает |
| `AUTH_ENABLED=true, но JWT_SECRET не задан` | задать `JWT_SECRET` в `.env`, затем `dc up -d` |
| `e2e_check.py`: вход → 401 | неверный `DEMO_USER_PASSWORD`, или пользователей нет: `dc exec api python scripts/create_demo_users.py` |
| браузер не открывает HTTPS | DNS домена ещё не обновился или закрыт порт 443; `dc logs caddy` покажет ошибку сертификата |
| порт 80 занят | в `.env` задать `HTTP_PORT=8080` (и `HTTPS_PORT`), открывать `http://<IP>:8080` |
| задача висит в `pending` | не работает worker: `dc ps`, `dc logs worker`, `dc restart worker` |
| интерфейс не открывается, API работает | `dc logs web`; обычно упала сборка фронта на `npm ci` (нет доступа к npm) — `dc build web` покажет ошибку |
| интерфейс открывается, данные не грузятся | `dc logs caddy` и `dc logs api`; запросы интерфейса идут на `/api/...` |

Остановить всё: `dc down` (данные базы сохранятся). Удалить вместе с базой: `dc down -v` (осторожно, данные пропадут).
