from sqlalchemy import select

from smartdiary.models import Memory, Record, User
from test_core import drain, payload


def test_decision_and_result_share_a_continuous_source_chain(harness, account):
    client, factory = harness
    decision = payload("我决定周末坐高铁去杭州，因为不想疲劳驾驶。")
    client.post("/v1/records", json=decision, headers=account)
    result = {
        **payload("后来准时到达，路上也休息得不错。", "2026-09-21"),
        "parent_record_id": decision["id"],
        "relation": "result",
    }
    assert client.post("/v1/records", json=result, headers=account).status_code == 200
    assert client.post("/v1/records", json=result, headers=account).json()["version"] == 1
    drain(factory)
    chain = client.get(f"/v1/records/{decision['id']}/timeline", headers=account).json()
    assert len(chain["items"]) == 2 and chain["items"][1]["kind"] == "result"
    answer = client.post(
        "/v1/ask", json={"question": "去杭州为什么坐高铁，后来怎样？"}, headers=account
    ).json()
    assert {s["record_id"] for s in answer["sources"]} == {decision["id"], result["id"]}
    other = client.post(
        "/v1/auth/register", json={"username": "other-person", "password": "a-long-password"}
    ).json()
    headers = {"Authorization": "Bearer " + other["token"]}
    assert client.get(f"/v1/records/{decision['id']}/timeline", headers=headers).status_code == 404
    assert (
        client.post(
            "/v1/records", json={**payload(), "parent_record_id": decision["id"]}, headers=headers
        ).status_code
        == 404
    )
    assert client.delete("/v1/records/" + decision["id"], headers=account).status_code == 200
    assert (
        client.get(f"/v1/records/{result['id']}/timeline", headers=account).json()["items"][0]["id"]
        == result["id"]
    )
    assert client.get("/v1/records/" + result["id"], headers=account).json()["parent_record_id"] is None


def test_event_correction_preserves_the_rest_of_the_same_raw_record(harness, account):
    client, factory = harness
    body = payload("我和小王喝了咖啡。我把钥匙放在红色柜子里。")
    client.post("/v1/records", json=body, headers=account)
    with factory() as db:
        record = db.get(Record, body["id"])
        user = db.scalar(select(User))
        start = record.text.index("我把钥匙")
        memory = Memory(
            user_id=user.id,
            record_id=record.id,
            record_version=1,
            title="钥匙位置",
            detail=record.text[start:],
            source_start=start,
            source_end=len(record.text),
            people=[],
            topics=[],
            kind="event",
        )
        db.add(memory)
        db.commit()
        mid = memory.id
    fixed = client.put(
        "/v1/memories/" + mid, headers=account, json={"title": "钥匙位置", "detail": "钥匙实际在蓝色柜子里。"}
    )
    assert fixed.status_code == 200
    record = client.get("/v1/records/" + body["id"], headers=account).json()
    assert (
        "小王喝了咖啡" in record["text"] and "蓝色柜子" in record["text"] and "红色柜子" not in record["text"]
    )
    assert "红色柜子" in record["original_text"]
    assert record["version"] == 2
    drain(factory)
    assert client.post("/v1/search", json={"question": "咖啡"}, headers=account).json()["results"]
