import uuid
from io import BytesIO

from PIL import Image

from smartdiary.ai import Bailian
from smartdiary.config import settings
from test_core import drain, payload


def png():
    output = BytesIO()
    Image.new("RGB", (4, 4), "red").save(output, format="PNG")
    return output.getvalue()


def fake_provider(monkeypatch):
    monkeypatch.setattr(settings, "ai_mode", "bailian")
    monkeypatch.setattr(Bailian, "image", lambda *args: "照片里有一辆红色自行车。")
    monkeypatch.setattr(Bailian, "json", lambda *args, **kwargs: {"events": []})
    monkeypatch.setattr(Bailian, "embeddings", lambda self, texts: [None for _ in texts])


def test_late_photo_keeps_corrected_text_and_adds_pending_diary_content(harness, account, monkeypatch):
    client, factory = harness
    body = payload("最初的一笔。")
    client.post("/v1/records", json=body, headers=account)
    client.post(
        "/v1/records", json={**body, "text": "我后来修正的文字。", "base_version": 1}, headers=account
    )
    drain(factory)
    diary = client.get("/v1/diaries", headers=account).json()[0]
    client.put(
        "/v1/diaries/2026-09-20",
        headers=account,
        json={
            "base_version": diary["version"],
            "paragraph_id": diary["paragraphs"][0]["id"],
            "text": "这一段是我自己写的。",
        },
    )
    aid = str(uuid.uuid4())
    client.post(
        f"/v1/records/{body['id']}/attachments/{aid}",
        files={"file": ("a.png", png(), "image/png")},
        headers=account,
    )
    fake_provider(monkeypatch)
    drain(factory)
    record = client.get("/v1/records/" + body["id"], headers=account).json()
    assert "我后来修正的文字" in record["text"] and "红色自行车" in record["text"]
    diary = client.get("/v1/diaries", headers=account).json()[0]
    assert diary["paragraphs"][0]["text"] == "这一段是我自己写的。"
    assert "红色自行车" in diary["pending"][0]["text"]
    client.post(f"/v1/records/{body['id']}/retry", headers=account)
    drain(factory)
    assert client.get("/v1/records/" + body["id"], headers=account).json()["text"].count("红色自行车") == 1


def test_image_extraction_persists_without_implicit_provider_commits(harness, account, monkeypatch):
    client, factory = harness
    body = {**payload(""), "kind": "image", "source_type": "external"}
    client.post("/v1/records", json=body, headers=account)
    aid = str(uuid.uuid4())
    client.post(
        f"/v1/records/{body['id']}/attachments/{aid}",
        files={"file": ("a.png", png(), "image/png")},
        headers=account,
    )
    fake_provider(monkeypatch)
    drain(factory)
    assert "红色自行车" in client.get("/v1/records/" + body["id"], headers=account).json()["text"]
    assert all(m["kind"] == "knowledge" for m in client.get("/v1/memories", headers=account).json())


def test_recording_segments_keep_capture_order_when_uploaded_out_of_order(harness, account, monkeypatch):
    from smartdiary.models import Attachment

    client, factory = harness
    body = {**payload(""), "kind": "audio"}
    client.post("/v1/records", json=body, headers=account)
    aids = [str(uuid.uuid4()), str(uuid.uuid4())]
    for position in [1, 0]:
        assert (
            client.post(
                f"/v1/records/{body['id']}/attachments/{aids[position]}",
                data={"position": position},
                files={"file": ("a.png", png(), "image/png")},
                headers=account,
            ).status_code
            == 200
        )
    with factory() as db:
        db.get(Attachment, aids[0]).extracted_text = "先做决定。"
        db.get(Attachment, aids[1]).extracted_text = "后来有了结果。"
        db.commit()
    fake_provider(monkeypatch)
    drain(factory)
    record = client.get("/v1/records/" + body["id"], headers=account).json()
    assert record["text"].index("先做决定") < record["text"].index("后来")
    assert [a["id"] for a in record["attachments"]] == aids


def test_phone_restore_upload_preserves_accepted_text(harness, account, monkeypatch):
    client, factory = harness
    fake_provider(monkeypatch)
    body = {**payload("照片实际是一辆蓝色自行车。"), "kind": "image"}
    client.post("/v1/records", json=body, headers=account)
    aid = str(uuid.uuid4())
    client.post(
        f"/v1/records/{body['id']}/attachments/{aid}",
        data={"preserve_text": "true"},
        files={"file": ("a.png", png(), "image/png")},
        headers=account,
    )
    drain(factory)
    record = client.get("/v1/records/" + body["id"], headers=account).json()
    assert record["text"] == body["text"] and record["attachments"][0]["preserve_text"]
