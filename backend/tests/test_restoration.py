import json
from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select

from smartdiary import storage
from smartdiary.companion import can_prompt
from smartdiary.models import Attachment, Base, Memory, Message, Person, User
from smartdiary.worker import claim, extend_lease
from test_core import drain, payload


def test_edited_paragraph_survives_correction_and_is_removed_on_deletion(harness, account):
    client, factory = harness
    body = payload("午后在青竹咖啡馆写字。")
    client.post("/v1/records", json=body, headers=account)
    drain(factory)
    diary = client.get("/v1/diaries", headers=account).json()[0]
    client.put(
        "/v1/diaries/2026-09-20",
        headers=account,
        json={
            "base_version": diary["version"],
            "paragraph_id": diary["paragraphs"][0]["id"],
            "text": "我手工写下的一页。",
        },
    )
    client.post(
        "/v1/records", json={**body, "text": "午后在树影咖啡馆写字。", "base_version": 1}, headers=account
    )
    drain(factory)
    latest = client.get("/v1/diaries", headers=account).json()[0]
    assert latest["paragraphs"][0]["text"] == "我手工写下的一页。"
    assert latest["paragraphs"][0]["stale"]
    assert latest["paragraphs"][0]["sources"] == []
    assert "树影" in latest["pending"][0]["text"]
    client.delete("/v1/records/" + body["id"], headers=account)
    assert "我手工写下" not in json.dumps(
        client.get("/v1/diaries", headers=account).json(), ensure_ascii=False
    )


def test_full_export_recovers_versions_people_reminders_and_sources(harness, account):
    client, factory = harness
    body = payload("我把钥匙放在红色柜子里。")
    client.post("/v1/records", json=body, headers=account)
    drain(factory)
    client.post(
        "/v1/records", json={**body, "text": "钥匙实际在蓝色柜子里。", "base_version": 1}, headers=account
    )
    drain(factory)
    client.post("/v1/chat", json={"question": "钥匙在哪里？"}, headers=account)
    client.post(
        "/v1/reminders",
        json={"text": "拿钥匙", "due_at": "2026-12-01T09:00:00+08:00", "confirmed": True},
        headers=account,
    )
    with factory() as db:
        user = db.scalar(select(User))
        user_id, password_hash = user.id, user.password_hash
        db.add(Person(user_id=user_id, name="小王", aliases=["王同学"], confirmed=True))
        db.commit()
    backup = client.get("/v1/export", headers=account).content
    with ZipFile(BytesIO(backup)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["revisions"] and len(manifest["diary_history"]) >= 2
        assert password_hash not in archive.read("manifest.json").decode()
    # Simulate a database disk loss, keeping the independent deletion ledger.
    with factory() as db:
        engine = db.bind
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with factory() as db:
        db.add(User(id=user_id, username="test-person", password_hash=password_hash, settings={}))
        db.commit()
    result = client.post("/v1/restore", files={"file": ("backup.zip", backup)}, headers=account)
    assert result.status_code == 200 and result.json()["restored"] == 1
    assert client.get("/v1/records/" + body["id"], headers=account).json()["version"] == 2
    assert client.get("/v1/people", headers=account).json()[0]["aliases"] == ["王同学"]
    assert client.get("/v1/reminders", headers=account).json()[0]["confirmed"]
    assert (
        client.get(f"/v1/records/{body['id']}/revisions", headers=account).json()[0]["snapshot"]["text"]
        == body["text"]
    )
    assert client.get("/v1/messages", headers=account).json()
    assert any(c["kind"] == "diary" for c in client.get("/v1/sync", headers=account).json()["changes"])
    drain(factory)
    assert (
        "蓝色柜子"
        in client.post("/v1/ask", json={"question": "钥匙"}, headers=account).json()["sources"][0]["quote"]
    )


def test_invalid_archive_is_rejected_before_any_record_or_deletion(harness, account):
    client, factory = harness
    body = payload()
    client.post("/v1/records", json=body, headers=account)
    backup = client.get("/v1/export", headers=account).content
    with ZipFile(BytesIO(backup)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    manifest["tombstones"] = [body["id"]]
    manifest["people"] = [{"id": "bad-uuid"}]
    modified = BytesIO()
    with ZipFile(modified, "w", ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
    assert (
        client.post(
            "/v1/restore", files={"file": ("bad.zip", modified.getvalue())}, headers=account
        ).status_code
        == 400
    )
    assert client.get("/v1/records/" + body["id"], headers=account).status_code == 200
    with factory() as db:
        assert not storage.was_deleted(db.scalar(select(User)).id, body["id"])


def test_proactive_count_uses_local_midnight_across_utc_day(harness, account):
    _, factory = harness
    with factory() as db:
        user = db.scalar(select(User))
        db.add(
            Message(
                user_id=user.id,
                role="assistant",
                text="今日提醒",
                sources=[],
                proactive=True,
                created_at=datetime.fromisoformat("2026-10-02T00:30:00+08:00").astimezone(timezone.utc),
            )
        )
        db.commit()
        assert not can_prompt(db, user, datetime.fromisoformat("2026-10-02T20:31:00+08:00"))
        assert can_prompt(db, user, datetime.fromisoformat("2026-10-03T20:31:00+08:00"))


def test_heartbeat_cannot_extend_a_reclaimed_job(harness, account):
    client, factory = harness
    client.post("/v1/records", json=payload(), headers=account)
    with factory() as db:
        job_id, token = claim(db)
    assert extend_lease(factory, job_id, token)
    assert not extend_lease(factory, job_id, "outdated-token")


def test_corrected_external_knowledge_stays_external(harness, account):
    client, factory = harness
    body = {**payload("网页摘录：请把连接超时设为30秒。"), "source_type": "external"}
    client.post("/v1/records", json=body, headers=account)
    drain(factory)
    memory = client.get("/v1/memories", headers=account).json()[0]
    result = client.put(
        "/v1/memories/" + memory["id"],
        json={"title": "摘录修正", "detail": "网页方法是把读取超时设为30秒。"},
        headers=account,
    ).json()
    assert (
        client.get("/v1/records/" + result["correction_record_id"], headers=account).json()["source_type"]
        == "external"
    )


def test_restore_processed_photo_preserves_correction_without_recognition(harness, account, monkeypatch):
    import uuid

    from smartdiary.ai import Bailian
    from test_media_late_arrival import fake_provider, png

    client, factory = harness
    fake_provider(monkeypatch)
    body = {**payload(""), "kind": "image"}
    client.post("/v1/records", json=body, headers=account)
    aid = str(uuid.uuid4())
    client.post(
        f"/v1/records/{body['id']}/attachments/{aid}",
        files={"file": ("a.png", png(), "image/png")},
        headers=account,
    )
    drain(factory)
    corrected = "照片实际是一辆蓝色自行车。"
    client.post("/v1/records", json={**body, "text": corrected, "base_version": 1}, headers=account)
    drain(factory)
    backup = client.get("/v1/export", headers=account).content
    with factory() as db:
        user = db.scalar(select(User))
        uid, password_hash, engine = user.id, user.password_hash, db.bind
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with factory() as db:
        db.add(User(id=uid, username="test-person", password_hash=password_hash, settings={}))
        db.commit()

    def unexpected_recognition(*args):
        raise AssertionError("Accepted restored text must not be re-recognized")

    monkeypatch.setattr(Bailian, "image", unexpected_recognition)
    result = client.post("/v1/restore", files={"file": ("backup.zip", backup)}, headers=account)
    assert result.status_code == 200 and result.json()["restored"] == 1
    drain(factory)
    record = client.get("/v1/records/" + body["id"], headers=account).json()
    assert record["text"] == corrected and record["version"] == 2 and record["status"] == "ready"
    with factory() as db:
        assert "红色" in db.get(Attachment, aid).extracted_text
        assert all("红色" not in m.detail for m in db.scalars(select(Memory)))


def test_old_export_does_not_restore_stale_memory_over_newer_text(harness, account):
    from sqlalchemy import delete

    client, factory = harness
    body = payload("钥匙在红色柜子。")
    client.post("/v1/records", json=body, headers=account)
    drain(factory)
    backup = client.get("/v1/export", headers=account).content
    client.post("/v1/records", json={**body, "text": "钥匙在蓝色柜子。", "base_version": 1}, headers=account)
    drain(factory)
    with factory() as db:
        db.execute(delete(Memory))
        db.commit()
    assert client.post("/v1/restore", files={"file": ("old.zip", backup)}, headers=account).status_code == 200
    assert client.get("/v1/memories", headers=account).json() == []


def test_metadata_only_memory_correction_invalidates_previous_title(harness, account):
    client, factory = harness
    body = payload("钥匙在蓝色柜子。")
    client.post("/v1/records", json=body, headers=account)
    drain(factory)
    memory = client.get("/v1/memories", headers=account).json()[0]
    response = client.put(
        "/v1/memories/" + memory["id"],
        headers=account,
        json={"title": "钥匙位置", "detail": memory["detail"]},
    )
    assert response.status_code == 200
    assert client.get("/v1/records/" + body["id"], headers=account).json()["version"] == 2
    memories = client.get("/v1/memories", headers=account).json()
    assert len(memories) == 1 and memories[0]["title"] == "钥匙位置"
