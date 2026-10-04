from smartdiary.config import settings
from test_core import drain, payload


def test_background_budget_failure_is_explicit_and_search_still_works(harness, account, monkeypatch):
    client, factory = harness
    body = payload("我在青竹咖啡馆写下了一个想法。")
    client.post("/v1/records", json=body, headers=account)
    monkeypatch.setattr(settings, "ai_mode", "deepseek")
    monkeypatch.setattr(settings, "deepseek_api_key", "not-a-real-key")
    monkeypatch.setattr(settings, "monthly_budget_yuan", 100)
    # Fixed costs + emergency reserve use all of this allowance, so no network request is made.
    drain(factory)
    record = client.get("/v1/records/" + body["id"], headers=account).json()
    assert record["status"] == "budget_blocked" and "额度不足" in record["error"]
    assert record["text"] == body["text"]
    assert client.post("/v1/search", json={"question": "青竹咖啡馆"}, headers=account).json()["results"]
