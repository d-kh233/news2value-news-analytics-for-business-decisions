"""Фоновый worker (B6): выполняет задачи из таблицы jobs. Запуск: python -m app.worker.

Когда задач нет, раз в WORKER_POLL_SECONDS:
  1. помечает зависшие задачи (running дольше JOB_TIMEOUT_MINUTES — прежний worker упал);
  2. берёт одну pending и выполняет конвейер (services/job_service.py → services/refresh.py).
Если задача была, следующая берётся сразу, без паузы.

Worker'ов можно запустить несколько: задачу захватывает SELECT ... FOR UPDATE SKIP LOCKED
(repositories/job_repo.py), одну задачу два worker'а не возьмут.
SIGTERM / SIGINT (docker compose stop, Ctrl+C) — новых задач не брать, текущую доделать.
Если процесс убит посреди задачи, её через JOB_TIMEOUT_MINUTES пометит failed любой worker.

Пульс (B8+B9): отдельный поток раз в HEARTBEAT_SECONDS обновляет время файла
WORKER_HEARTBEAT_FILE — по нему healthcheck в docker-compose.prod.yml видит, что процесс жив
(в том числе во время долгой задачи).
"""

import logging
import os
import signal
import threading
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services import job_service

logger = logging.getLogger("app.worker")

HEARTBEAT_SECONDS = 10.0


def heartbeat_file() -> Path:
    return Path(os.environ.get("WORKER_HEARTBEAT_FILE", "/tmp/worker-heartbeat"))


def heartbeat(stop: threading.Event, every: float = HEARTBEAT_SECONDS) -> None:
    """Пока worker работает, обновлять время файла-пульса."""
    path = heartbeat_file()
    while not stop.is_set():
        try:
            path.touch()
        except OSError:
            logger.warning("не удалось обновить файл-пульс %s", path, exc_info=True)
        stop.wait(every)


def run_once() -> bool:
    """Один проход: зависшие → failed, затем одна задача. True — задача была выполнена."""
    job_service.fail_stale()
    job = job_service.claim_next()
    if job is None:
        return False
    logger.info("задача %s: %s %s %s", job.id, job.kind, job.company_id, job.params)
    status = job_service.execute(job)
    logger.info("задача %s: %s", job.id, status)
    return True


def run(stop: threading.Event, poll_seconds: float | None = None) -> None:
    """Цикл до stop. Ошибка прохода (например, БД недоступна) — в лог и повтор после паузы."""
    poll = get_settings().worker_poll_seconds if poll_seconds is None else poll_seconds
    while not stop.is_set():
        try:
            busy = run_once()
        except Exception:  # noqa: BLE001 — worker не должен падать из-за одного прохода
            logger.exception("ошибка прохода worker, повтор через %s с", poll)
            busy = False
        if not busy:
            stop.wait(poll)


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    stop = threading.Event()

    def on_signal(signum: int, _frame: object) -> None:
        logger.info("сигнал %s: новых задач не беру, выхожу после текущей", signum)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    logger.info(
        "worker запущен: опрос раз в %s с, зависшая задача — дольше %s мин, ML_MODE=%s",
        settings.worker_poll_seconds,
        settings.job_timeout_minutes,
        settings.ml_mode,
    )
    threading.Thread(target=heartbeat, args=(stop,), daemon=True, name="heartbeat").start()
    run(stop)
    logger.info("worker остановлен")


if __name__ == "__main__":
    main()
