import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smartdiary.app import app
from smartdiary.config import settings
from smartdiary.db import Base, get_db, make_engine


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "storage")
    settings.data_dir.mkdir()
    monkeypatch.setattr(settings, "ai_mode", "disabled")
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "registration_token", "")
    monkeypatch.setattr(settings, "fixed_monthly_cost_yuan", 80)
    monkeypatch.setattr(settings, "monthly_budget_yuan", 300)
    monkeypatch.setattr(settings, "reserve_yuan", 20)
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        assert test_url.endswith("/smartdiary_test"), (
            "Integration tests require the dedicated smartdiary_test database"
        )
    engine = make_engine(test_url or "sqlite:///" + str(tmp_path / "test.db"))
    if test_url:
        from sqlalchemy import text

        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    def db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = db
    with TestClient(app) as client:
        yield client, factory
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def account(harness):
    client, _ = harness
    response = client.post(
        "/v1/auth/register", json={"username": "test-person", "password": "a-good-password"}
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}
