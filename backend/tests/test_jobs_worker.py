"""Фоновые задачи refresh (B6): таблица jobs, worker, POST .../refresh и GET /jobs/{id}.

Worker в тестах крутится в потоке этого же процесса (app.worker.run с флагом остановки).
Генератор — заглушка ML_MODE=fake на новостях из documents (фикстура news в conftest):
у U_2 (напитки) одна карточка, поэтому при FAKE_DELAY_SECONDS=0.5 задача ~0,5 с в running.
"""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app import worker
from app.ml_ports import fake_port
from app.repositories import job_repo, pipeline_run_repo
from app.services import job_service

DRINKS = "U_2"
SOFTWARE = "U_4"


@pytest.fixture
def fake(news, env):
    """Заглушка на новостях из БД, без паузы (тест задаёт FAKE_DELAY_SECONDS сам)."""
    env(ML_MODE="fake", NEWS_SOURCE="db", FAKE_DELAY_SECONDS=0)
    return news


def _refresh(client, company_id: str = DRINKS, **params) -> str:
    r = client.post(f"/companies/{company_id}/insights/refresh", params=params)
    assert r.status_code == 202, r.text
    assert set(r.json()) == {"job_id"}
    return r.json()["job_id"]


def _job(client, job_id: str) -> dict:
    r = client.get(f"/jobs/{job_id}")
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"id", "status", "error"}  # схему Job не расширяем
    return r.json()


def _wait(client, job_id: str, until: set[str], seen: list[str], timeout: float = 15) -> None:
    """Поллинг GET /jobs/{id}, пока статус не попадёт в until; смена статусов — в seen."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _job(client, job_id)["status"]
        if not seen or seen[-1] != status:
            seen.append(status)
        if status in until:
            return
        time.sleep(0.02)
    raise AssertionError(f"задача {job_id} не дошла до {until}, статусы: {seen}")


@contextmanager
def running_worker() -> Iterator[None]:
    stop = threading.Event()
    thread = threading.Thread(target=worker.run, kwargs={"stop": stop, "poll_seconds": 0.05})
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=30)
        assert not thread.is_alive(), "worker не остановился"


def _count(db: Engine, table: str) -> int:
    with db.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


# ---------- POST refresh ----------


def test_refresh_answers_fast_and_only_creates_job(fake, bank_client, db, env):
    """API только ставит задачу: ответ быстрее секунды даже при паузе заглушки 15 с."""
    env(FAKE_DELAY_SECONDS=15)
    t0 = time.perf_counter()
    job_id = _refresh(bank_client)
    assert time.perf_counter() - t0 < 1
    assert job_id.startswith("job-")
    assert _job(bank_client, job_id) == {"id": job_id, "status": "pending", "error": None}
    assert _count(db, "pipeline_runs") == 0  # конвейер в процессе API не запускался


def test_status_goes_pending_running_done(fake, bank_client, db, env):
    env(FAKE_DELAY_SECONDS=0.5)
    job_id = _refresh(bank_client, role="cfo")
    seen = [_job(bank_client, job_id)["status"]]
    with running_worker():
        _wait(bank_client, job_id, {"done", "failed"}, seen)
    assert seen == ["pending", "running", "done"]
    assert _job(bank_client, job_id)["error"] is None
    with db.connect() as conn:
        roles = conn.execute(text("SELECT role FROM insights WHERE company_id = 'U_2'")).scalars()
        assert list(roles) == ["cfo"]
        run = conn.execute(
            text(
                "SELECT r.status FROM jobs j JOIN pipeline_runs r ON r.id = j.run_id "
                "WHERE j.id = :id"
            ),
            {"id": job_id},
        ).scalar_one()
    assert run == "done"


def test_repeat_refresh_while_active_returns_same_job(fake, bank_client, env):
    """Пока у компании есть pending/running задача, новая не создаётся (роль не важна)."""
    env(FAKE_DELAY_SECONDS=0.5)
    first = _refresh(bank_client)
    assert _refresh(bank_client, role="cfo") == first  # pending
    seen: list[str] = []
    with running_worker():
        _wait(bank_client, first, {"running"}, seen)
        assert _refresh(bank_client) == first  # running
        _wait(bank_client, first, {"done", "failed"}, seen)
    assert seen[-1] == "done"
    # у другой компании своя задача, а после завершения — снова новая
    assert _refresh(bank_client, SOFTWARE) != first
    assert _refresh(bank_client) != first


def test_refresh_unknown_company_404(db_client):
    for company_id in ("nope", "c-001"):  # моковых компаний в БД нет — инсайты пишутся в БД
        r = db_client.post(f"/companies/{company_id}/insights/refresh")
        assert r.status_code == 404
        assert r.json()["error"] == "not_found"


def test_refresh_unknown_role_422(db_client):
    r = db_client.post(f"/companies/{DRINKS}/insights/refresh", params={"role": "intern"})
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


# ---------- GET /jobs ----------


def test_job_not_found(db_client):
    r = db_client.get("/jobs/job-00000000")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_refresh_job_has_no_ask_result(fake, bank_client):
    job_id = _refresh(bank_client)
    worker.run_once()
    assert _job(bank_client, job_id)["status"] == "done"
    r = bank_client.get(f"/jobs/{job_id}/result")
    assert r.status_code == 404 and r.json()["error"] == "not_found"


def test_job_run_has_plan_and_trace(fake, bank_client):
    job_id = _refresh(bank_client, role="treasurer")
    assert bank_client.get(f"/jobs/{job_id}/run").status_code == 404  # ещё не выполнена
    assert worker.run_once() is True
    run = bank_client.get(f"/jobs/{job_id}/run").json()
    assert run["role"] == "treasurer" and run["llm_mode"] == "fake" and run["run_id"]
    assert run["plan"] and [s["name"] for s in run["trace"]] == [
        "Поиск новостей",
        "Совпадение слов с профилем",
    ]


def test_failed_pipeline_gives_failed_job(fake, bank_client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(fake_port, "generate_insights", boom)
    job_id = _refresh(bank_client)
    worker.run_once()
    job = _job(bank_client, job_id)
    assert job["status"] == "failed" and "модель недоступна" in job["error"]
    assert bank_client.get(f"/jobs/{job_id}/run").status_code == 404


def test_empty_run_is_done_job(bank_client, modes):
    """0 карточек (status=empty у pipeline_runs) — задача выполнена, а не упала."""
    modes("fake", "db")  # новостей в documents нет
    job_id = _refresh(bank_client)
    worker.run_once()
    assert _job(bank_client, job_id) == {"id": job_id, "status": "done", "error": None}


def test_company_deleted_before_run_fails_job(db_client):
    created = db_client.post(
        "/companies",
        json={"name": "Ромашка", "industry": "Торговля", "region": "Москва", "revenue_bln_rub": 1},
    )
    company_id = created.json()["id"]
    job_id = _refresh(db_client, company_id)
    assert db_client.delete(f"/companies/{company_id}").status_code == 204
    # задача удалилась вместе с компанией (внешний ключ ON DELETE CASCADE)
    assert db_client.get(f"/jobs/{job_id}").status_code == 404
    assert worker.run_once() is False


def test_ask_jobs_still_work(client, insight_id):
    """ask пока на моках: его задачи GET /jobs/{id} находит в памяти, без БД."""
    r = client.post(f"/insights/{insight_id}/ask", json={"question": "Что делать?"})
    job_id = r.json()["job_id"]
    assert _job(client, job_id)["status"] == "done"


# ---------- worker: захват задач и зависшие задачи ----------


def test_run_once_without_jobs(db_client):
    assert worker.run_once() is False


def test_two_workers_do_not_take_same_job(fake, db):
    """SELECT ... FOR UPDATE SKIP LOCKED: занятую строку второй worker пропускает, не ждёт."""
    first = job_service.request_refresh(DRINKS, None)
    second = job_service.request_refresh(SOFTWARE, None)
    with Session(db) as a, Session(db) as b, Session(db) as c:
        job_a = job_repo.claim_next(a)  # транзакции не закрыты: строки ещё под замком
        job_b = job_repo.claim_next(b)
        t0 = time.perf_counter()
        job_c = job_repo.claim_next(c)
        assert time.perf_counter() - t0 < 1
        assert job_a is not None and job_b is not None and job_c is None
        assert {job_a.id, job_b.id} == {first, second}
        a.commit()
        b.commit()
    assert worker.run_once() is False  # обе уже running


def test_stale_running_job_becomes_failed(fake, db):
    stale = job_service.request_refresh(DRINKS, None)
    fresh = job_service.request_refresh(SOFTWARE, None)
    with Session(db) as session:
        for _ in range(2):
            job = job_repo.claim_next(session)
            run_id = pipeline_run_repo.create(session, job.company_id)
            job_repo.set_run_id(session, job.id, run_id)
        session.execute(
            text("UPDATE jobs SET started_at = now() - interval '11 minutes' WHERE id = :id"),
            {"id": stale},
        )
        session.commit()

    assert job_service.fail_stale() == [stale]
    with db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT j.id, j.status, j.error, j.finished_at, r.status AS run_status "
                "FROM jobs j JOIN pipeline_runs r ON r.id = j.run_id"
            )
        ).mappings()
        jobs = {r["id"]: r for r in rows}
    assert jobs[stale]["status"] == "failed" and jobs[stale]["run_status"] == "failed"
    assert "10 мин" in jobs[stale]["error"] and jobs[stale]["finished_at"] is not None
    assert jobs[fresh]["status"] == "running" and jobs[fresh]["run_status"] == "running"
    # worker, который всё-таки доработал, не затирает failed
    with Session(db) as session:
        assert job_repo.finish(session, stale, "done") is False
        assert job_repo.finish(session, fresh, "done") is True
        session.commit()


def test_failed_stale_job_frees_company(fake, bank_client, db):
    job_id = _refresh(bank_client)
    with Session(db) as session:
        job_repo.claim_next(session)
        session.execute(text("UPDATE jobs SET started_at = now() - interval '1 hour'"))
        session.commit()
    worker.run_once()  # сначала помечает зависшие, потом ищет pending — её нет
    assert _job(bank_client, job_id)["status"] == "failed"
    assert _refresh(bank_client) != job_id
