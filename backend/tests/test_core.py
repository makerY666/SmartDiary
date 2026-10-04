import json
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from zipfile import ZipFile

from PIL import Image
from sqlalchemy import select

from smartdiary.budget import BudgetExceeded, reserve
from smartdiary.companion import in_quiet_hours
from smartdiary.config import settings
from smartdiary.models import Job, Memory, Record, Usage, User
from smartdiary.schemas import Preferences
from smartdiary.worker import claim, run_once, schedule


def payload(text="今天和小王在蜀香川菜馆吃了水煮鱼。", day="2026-09-20"):
    return {
        "id": str(uuid.uuid4()),
        "text": text,
        "occurred_at": day + "T12:00:00+08:00",
        "recorded_at": day + "T12:01:00+08:00",
        "kind": "text",
        "base_version": 0,
    }


def drain(factory):
    for _ in range(50):
        if not run_once(factory):
            return
    raise AssertionError("worker did not drain")


def test_register_login_and_user_isolation(harness, account):
    client, factory = harness
    body = payload()
    assert client.post("/v1/records", json=body, headers=account).status_code == 200
    other = client.post(
        "/v1/auth/register", json={"username": "other-user", "password": "another-good-password"}
    ).json()
    headers = {"Authorization": "Bearer " + other["token"]}
    assert client.get("/v1/records/" + body["id"], headers=headers).status_code == 404
    assert client.post("/v1/records", json=body, headers=headers).status_code == 404
    drain(factory)
    assert client.post("/v1/ask", json={"question": "川菜馆"}, headers=headers).json()["sources"] == []
    assert client.get("/v1/sync", headers=headers).json()["changes"] == []
    assert client.get("/v1/records").status_code == 401
    assert (
        client.post(
            "/v1/auth/login", json={"username": "test-person", "password": "a-good-password"}
        ).status_code
        == 200
    )


def test_idempotency_conflict_and_correction(harness, account):
    client, factory = harness
    body = payload()
    assert client.post("/v1/records", json=body, headers=account).json()["version"] == 1
    assert client.post("/v1/records", json=body, headers=account).json()["version"] == 1
    conflict = {**body, "text": "另一台手机写下的版本"}
    assert client.post("/v1/records", json=conflict, headers=account).status_code == 409
    revisions = client.get(f"/v1/records/{body['id']}/revisions", headers=account).json()
    assert revisions[0]["snapshot"]["text"] == conflict["text"]
    drain(factory)
    corrected = {**body, "text": "今天在青竹咖啡馆喝咖啡。", "base_version": 1}
    assert client.post("/v1/records", json=corrected, headers=account).json()["version"] == 2
    assert client.post("/v1/search", json={"question": "川菜馆"}, headers=account).json()["results"] == []
    drain(factory)
    answer = client.post("/v1/ask", json={"question": "青竹咖啡"}, headers=account).json()
    assert answer["sources"][0]["version"] == 2
    assert "青竹咖啡" in answer["sources"][0]["quote"]


def test_diary_dual_views_manual_edits_and_late_record(harness, account):
    client, factory = harness
    body = payload()
    client.post("/v1/records", json=body, headers=account)
    drain(factory)
    diary = client.post("/v1/diaries/2026-09-20/generate", headers=account).json()
    assert diary["paragraphs"][0]["sources"] == diary["timeline"][0]["sources"]
    edit = {
        "base_version": diary["version"],
        "paragraph_id": diary["paragraphs"][0]["id"],
        "text": "这是我修改后的日记。",
    }
    assert client.put("/v1/diaries/2026-09-20", json=edit, headers=account).status_code == 200
    late = payload("晚上在河边散步，看到了月亮。")
    client.post("/v1/records", json=late, headers=account)
    drain(factory)
    latest = client.get("/v1/diaries", headers=account).json()[0]
    assert latest["paragraphs"][0]["text"] == edit["text"]
    assert any("月亮" in p["text"] for p in latest["pending"])
    result = client.post(f"/v1/diaries/2026-09-20/merge?version={latest['version']}", headers=account).json()
    assert result["pending"] == []
    assert any("月亮" in p["text"] for p in result["paragraphs"])


def test_external_material_not_personal_and_empty_day(harness, account):
    client, factory = harness
    body = {**payload("书中摘录：坚持写作比等灵感更重要。"), "source_type": "external"}
    client.post("/v1/records", json=body, headers=account)
    drain(factory)
    diary = client.post("/v1/diaries/2026-09-20/generate", headers=account).json()
    assert diary["paragraphs"][0]["kind"] == "external"
    assert diary["timeline"][0]["kind"] == "knowledge"
    assert client.post("/v1/diaries/2025-01-01/generate", headers=account).json()["version"] == 0


def test_delete_purges_sources_export_and_old_sync(harness, account):
    client, factory = harness
    body = payload("私密暗号：蓝鲸秘密。")
    client.post("/v1/records", json=body, headers=account)
    drain(factory)
    backup = client.get("/v1/export", headers=account).content
    assert client.delete("/v1/records/" + body["id"], headers=account).status_code == 200
    assert client.post("/v1/records", json=body, headers=account).status_code == 410
    assert client.post("/v1/ask", json={"question": "蓝鲸秘密"}, headers=account).json()["sources"] == []
    assert "蓝鲸秘密" not in json.dumps(client.get("/v1/sync", headers=account).json(), ensure_ascii=False)
    export = client.get("/v1/export", headers=account).content
    with ZipFile(BytesIO(export)) as bundle:
        assert "蓝鲸秘密" not in bundle.read("manifest.json").decode()
    restored = client.post("/v1/restore", files={"file": ("old.zip", backup)}, headers=account).json()
    assert restored["restored"] == 0
    with factory() as db:
        assert not list(db.scalars(select(Memory)))
        assert db.get(Record, body["id"]).text == ""


def test_real_attachment_validation_encryption_idempotency(harness, account):
    client, factory = harness
    body = {**payload(""), "kind": "image"}
    client.post("/v1/records", json=body, headers=account)
    output = BytesIO()
    Image.new("RGB", (4, 4), "red").save(output, format="PNG")
    image = output.getvalue()
    aid = str(uuid.uuid4())
    path = f"/v1/records/{body['id']}/attachments/{aid}"
    assert (
        client.post(path, files={"file": ("a.png", image, "image/png")}, headers=account).status_code == 200
    )
    assert (
        client.post(path, files={"file": ("a.png", image, "image/png")}, headers=account).status_code == 200
    )
    assert client.get("/v1/attachments/" + aid, headers=account).content == image
    assert (
        client.post(
            path.replace(aid, str(uuid.uuid4())),
            files={"file": ("fake.png", b"bad", "image/png")},
            headers=account,
        ).status_code
        == 415
    )
    stored = next((settings.data_dir / "attachments").rglob("*.enc")).read_bytes()
    assert stored != image
    drain(factory)
    assert client.get("/v1/records/" + body["id"], headers=account).json()["status"] == "needs_ai"


def test_worker_reclaims_expired_lease(harness, account):
    client, factory = harness
    body = payload()
    client.post("/v1/records", json=body, headers=account)
    with factory() as db:
        first = claim(db)
        assert first is not None
        assert claim(db) is None
        job = db.get(Job, first[0])
        job.lease_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        second = claim(db)
        assert second[0] == first[0] and second[1] != first[1]


def test_budget_reservations_and_failed_calls(harness, account):
    _, factory = harness
    with factory() as db:
        user = db.scalar(select(User))
        reserve(db, user.id, "test", 199)
        try:
            reserve(db, user.id, "test", 2)
            assert False, "reservation exceeded budget"
        except BudgetExceeded:
            pass
        assert len(list(db.scalars(select(Usage)))) == 1


def test_quiet_modes_timezones_and_confirmed_reminders(harness, account):
    client, factory = harness
    prefs = Preferences(proactivity="quiet")
    assert in_quiet_hours(prefs, datetime.fromisoformat("2026-10-02T23:00:00+08:00"))
    assert not in_quiet_hours(prefs, datetime.fromisoformat("2026-10-02T10:00:00+08:00"))
    assert client.put("/v1/settings", json=prefs.model_dump(), headers=account).status_code == 200
    schedule(factory, datetime.fromisoformat("2026-10-02T21:00:00+08:00"))
    assert client.get("/v1/messages", headers=account).json() == []
    reminder = {"text": "给朋友回电话", "due_at": "2026-09-01T10:00:00+08:00"}
    assert client.post("/v1/reminders", json=reminder, headers=account).status_code == 422
    assert (
        client.post("/v1/reminders", json={**reminder, "confirmed": True}, headers=account).status_code == 200
    )
    schedule(factory)
    assert client.get("/v1/messages", headers=account).json()[0]["text"] == reminder["text"]


def test_no_evidence_and_invalid_dates(harness, account):
    client, _ = harness
    result = client.post("/v1/ask", json={"question": "我的银行卡密码"}, headers=account).json()
    assert result["uncertain"] and result["sources"] == []
    assert (
        client.post("/v1/search", json={"question": "咖啡", "day_from": "bad"}, headers=account).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/records", json={**payload(), "occurred_at": "2026-10-02T12:00:00"}, headers=account
        ).status_code
        == 422
    )


def test_memory_correction_is_user_evidence(harness, account):
    client, factory = harness
    body = payload("我把钥匙放在红色柜子里。")
    client.post("/v1/records", json=body, headers=account)
    drain(factory)
    memory = client.get("/v1/memories", headers=account).json()[0]
    corrected = client.put(
        "/v1/memories/" + memory["id"],
        json={"title": "钥匙位置修正", "detail": "钥匙实际放在蓝色柜子里。"},
        headers=account,
    )
    assert corrected.status_code == 200
    results = client.post("/v1/search", json={"question": "红色柜子"}, headers=account).json()["results"]
    assert all("红色柜子" not in r["source"]["quote"] for r in results)
    drain(factory)
    result = client.post("/v1/ask", json={"question": "钥匙"}, headers=account).json()
    assert "蓝色柜子" in result["sources"][0]["quote"]
