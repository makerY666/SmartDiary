from sqlalchemy import select

from smartdiary.ai import AIUnavailable, Bailian
from smartdiary.config import settings
from smartdiary.domain import erase_record
from smartdiary.models import User
from test_core import drain, payload


def test_linked_chat_is_invalidated_with_its_source(harness, account):
    client, factory = harness
    body = payload("咖啡馆的门禁暗号是蓝鲸。")
    client.post("/v1/records", json=body, headers=account)
    client.post("/v1/chat", json={"question": body["text"], "record_id": body["id"]}, headers=account)
    client.delete("/v1/records/" + body["id"], headers=account)
    assert all("蓝鲸" not in m["text"] for m in client.get("/v1/messages", headers=account).json())


def test_failed_model_call_cannot_return_deleted_evidence(harness, account, monkeypatch):
    client, factory = harness
    body = payload("咖啡馆的门禁暗号是蓝鲸。")
    client.post("/v1/records", json=body, headers=account)
    drain(factory)
    monkeypatch.setattr(settings, "ai_mode", "deepseek")

    def failed(*args, **kwargs):
        with factory() as db:
            erase_record(db, db.scalar(select(User)), body["id"])
        raise AIUnavailable("模型超时")

    monkeypatch.setattr(Bailian, "json", failed)
    result = client.post("/v1/ask", json={"question": "蓝鲸"}, headers=account).json()
    assert result["sources"] == [] and result["mode"] == "stale"


def test_date_filter_includes_local_early_morning(harness, account):
    client, factory = harness
    body = payload("凌晨看到了银河。")
    body["occurred_at"] = "2026-09-20T00:30:00+08:00"
    client.post("/v1/records", json=body, headers=account)
    assert client.post(
        "/v1/search",
        json={"question": "银河", "day_from": "2026-09-20", "day_to": "2026-09-20"},
        headers=account,
    ).json()["results"]


def test_long_record_keeps_full_source_in_companion_context(harness, account, monkeypatch):
    from smartdiary.companion import respond

    client, factory = harness
    body = payload("沿路走走。" * 500 + "最后在青竹咖啡馆休息。")
    client.post("/v1/records", json=body, headers=account)
    monkeypatch.setattr(settings, "ai_mode", "deepseek")

    def reply(self, instruction, data, **kwargs):
        assert data["evidence"][0]["quote"] == body["text"]
        return {"reply": "最后在青竹咖啡馆休息了。", "record_ids": [body["id"]], "questions": []}

    monkeypatch.setattr(Bailian, "json", reply)
    with factory() as db:
        message = respond(db, db.scalar(select(User)), body["text"], record_id=body["id"])
        assert message.sources[0]["quote"] == body["text"]
