from datetime import datetime

from sqlalchemy import select

from smartdiary.ai import Bailian
from smartdiary.companion import respond, weekly_prompt
from smartdiary.config import settings
from smartdiary.models import Job, Usage, User
from smartdiary.worker import schedule
from test_core import payload


def test_operation_metrics_are_scoped_and_contain_no_diary_text(harness, account):
    client, factory = harness
    with factory() as db:
        user = db.scalar(select(User))
        db.add(
            Usage(
                user_id=user.id,
                operation="answer",
                month="2026-10",
                reserved_yuan=0,
                spent_yuan=0.001,
                latency_ms=1234,
                state="failed",
            )
        )
        db.commit()
    report = client.get("/v1/metrics", headers=account).json()
    assert report["operations"][0]["calls"] == 1
    assert report["operations"][0]["failures"] == 1
    assert report["operations"][0]["mean_latency_ms"] == 1234
    other = client.post(
        "/v1/auth/register", json={"username": "other-person", "password": "a-long-password"}
    ).json()
    assert (
        client.get("/v1/metrics", headers={"Authorization": "Bearer " + other["token"]}).json()["operations"]
        == []
    )


def test_sunday_weekly_review_has_sources_and_respects_daily_cap(harness, account, monkeypatch):
    client, factory = harness
    body = payload("周六和小王在青竹咖啡馆复盘了旅行。", "2026-10-03")
    client.post("/v1/records", json=body, headers=account)
    now = datetime.fromisoformat("2026-10-04T20:31:00+08:00")
    monkeypatch.setattr(settings, "ai_mode", "deepseek")
    monkeypatch.setattr("smartdiary.companion.utcnow", lambda: now)
    monkeypatch.setattr(
        Bailian,
        "json",
        lambda self, instruction, data, **kwargs: {
            "text": "周六和小王复盘了旅行。",
            "record_ids": [body["id"]],
        },
    )
    schedule(factory, now)
    with factory() as db:
        assert db.scalar(select(Job).where(Job.kind == "weekly"))
        user = db.scalar(select(User))
        review = weekly_prompt(db, user)
        assert review.sources[0]["record_id"] == body["id"]
    assert weekly_prompt(db, user) is None


def test_switching_to_quiet_during_model_call_cancels_proactive_reply(harness, account, monkeypatch):
    client, factory = harness
    body = payload("我今天复盘了旅行。")
    client.post("/v1/records", json=body, headers=account)
    monkeypatch.setattr(settings, "ai_mode", "deepseek")
    monkeypatch.setattr(
        "smartdiary.companion.utcnow", lambda: datetime.fromisoformat("2026-10-03T12:00:00+08:00")
    )

    def change_mode(self, instruction, data, **kwargs):
        with factory() as other:
            user = other.scalar(select(User))
            user.settings = {"proactivity": "quiet"}
            other.commit()
        return {"reply": "可以聊聊旅行。", "record_ids": [body["id"]], "questions": []}

    monkeypatch.setattr(Bailian, "json", change_mode)
    with factory() as db:
        user = db.scalar(select(User))
        assert respond(db, user, body["text"], proactive=True, record_id=body["id"]) is None
    assert client.get("/v1/messages", headers=account).json() == []
