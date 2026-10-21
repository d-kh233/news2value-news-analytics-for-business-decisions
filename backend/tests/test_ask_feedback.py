from app.schemas.job import Job
from app.schemas.qa import AskResult, Feedback


def test_ask_flow(client, insight_id):
    r = client.post(
        f"/insights/{insight_id}/ask", json={"question": "Что делать в первую очередь?"}
    )
    assert r.status_code == 202
    assert set(r.json()) == {"job_id"}
    job_id = r.json()["job_id"]

    job = Job.model_validate(client.get(f"/jobs/{job_id}").json())
    assert job.status == "done"

    res = client.get(f"/jobs/{job_id}/result")
    assert res.status_code == 200
    result = AskResult.model_validate(res.json())
    assert result.answer
    assert set(res.json()) == {"answer", "sources"}


def test_ask_validation_and_404(client, insight_id):
    assert client.post(f"/insights/{insight_id}/ask", json={"question": ""}).status_code == 422
    assert client.post(f"/insights/{insight_id}/ask", json={}).status_code == 422
    r = client.post("/insights/nope/ask", json={"question": "Привет?"})
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_first_feedback_201_then_update_200(client, insight_id):
    url = f"/insights/{insight_id}/feedback"
    first = client.post(url, json={"rating": "useful", "comment": "Полезно"})
    assert first.status_code == 201
    fb = Feedback.model_validate(first.json())
    assert fb.insight_id == insight_id and fb.rating == "useful"

    second = client.post(url, json={"rating": "not_useful", "comment": None})
    assert second.status_code == 200
    updated = Feedback.model_validate(second.json())
    assert updated.rating == "not_useful" and updated.comment is None

    third = client.post(url, json={"rating": "useful"})
    assert third.status_code == 200


def test_feedback_on_other_insight_is_201(client, company_id):
    items = client.get(f"/companies/{company_id}/insights").json()["items"]
    a, b = items[0]["id"], items[1]["id"]
    assert client.post(f"/insights/{a}/feedback", json={"rating": "useful"}).status_code == 201
    assert client.post(f"/insights/{b}/feedback", json={"rating": "useful"}).status_code == 201


def test_feedback_comment_limit(client, insight_id):
    url = f"/insights/{insight_id}/feedback"
    ok = client.post(url, json={"rating": "useful", "comment": "я" * 500})
    assert ok.status_code == 201
    too_long = client.post(url, json={"rating": "useful", "comment": "я" * 501})
    assert too_long.status_code == 422
    assert too_long.json()["error"] == "validation_error"


def test_feedback_validation_and_404(client, insight_id):
    assert (
        client.post(f"/insights/{insight_id}/feedback", json={"rating": "meh"}).status_code == 422
    )
    assert client.post("/insights/nope/feedback", json={"rating": "useful"}).status_code == 404
