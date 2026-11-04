from __future__ import annotations

import asyncio
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import csrf_valid, create_csrf_token, create_session, hash_password, session_valid, verify_password
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.integrations.rea import McpServerConfig, READ_ONLY_TOOL_NAMES, rea_service
from app.main import app
from app.models import Application, Job, ProfessionalProfile


def test_password_hash_session_and_csrf():
    get_settings().app_password_hash = hash_password("test-only-password-long-enough")
    assert verify_password("test-only-password-long-enough", get_settings().app_password_hash)
    assert not verify_password("incorrect-password", get_settings().app_password_hash)
    token = create_session()
    csrf = create_csrf_token(token)
    assert session_valid(token)
    assert csrf_valid(token, csrf, csrf)
    assert not csrf_valid(token, csrf, "tampered")
    assert not session_valid(token + "x")


def test_configuration_limits_app_and_cdp_to_loopback():
    try:
        Settings(app_host="0.0.0.0")
        assert False, "public listen address must be rejected"
    except ValueError:
        pass
    try:
        Settings(rea_cdp_endpoint="http://192.0.2.1:9222")
        assert False, "non-loopback CDP must be rejected"
    except ValueError:
        pass
    try:
        Settings(frontend_origin="https://example.org")
        assert False, "non-local frontend origin must be rejected"
    except ValueError:
        pass


def test_codex_rea_configuration_is_stdio_and_tool_allowlist_is_readonly():
    config = McpServerConfig.load_codex_server()
    assert config.command
    assert config.args
    assert config.config_path
    assert "list_browser_targets" in READ_ONLY_TOOL_NAMES
    assert "capture_browser_scenario" not in READ_ONLY_TOOL_NAMES


def test_api_auth_profile_job_duplicate_and_application_model():
    get_settings().app_password_hash = hash_password("test-only-password-long-enough")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    with TestingSession() as db:
        db.add(ProfessionalProfile(id=1))
        db.commit()

    def override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    try:
        async def exercise_api():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                assert (await client.get("/api/health")).json()["authentication_configured"] is True
                assert (await client.get("/api/profile")).status_code == 401
                assert (await client.get("/docs")).status_code == 404
                login = await client.post(
                    "/api/auth/login",
                    json={"password": "test-only-password-long-enough"},
                    headers={"Origin": get_settings().frontend_origin},
                )
                assert login.status_code == 200
                csrf = login.json()["csrf_token"]
                rea_status = await client.get("/api/rea/status")
                assert rea_status.status_code == 200
                assert rea_status.json()["state"] == "connected"
                assert rea_status.json()["tool_count"] > 0
                targets = await client.get("/api/rea/targets?origin=https://example.com")
                assert targets.status_code == 200, targets.text
                target_rows = targets.json()["structuredContent"]["normalized_result"]["targets"]
                selected = next(target for target in target_rows if target["url"] == "https://example.com/")
                inspection = await client.post(
                    "/api/rea/inspect",
                    json={"target_id": selected["target_id"], "origin": "https://example.com"},
                    headers={"Origin": get_settings().frontend_origin, "X-CSRF-Token": csrf},
                )
                assert inspection.status_code == 200
                page = inspection.json()["structuredContent"]["normalized_result"]
                assert page["target"]["title"] == "Example Domain"
                assert page["dom"]["total_nodes"] > 0
                assert page["accessibility"]["total_nodes"] > 0
                profile_response = await client.get("/api/profile")
                assert profile_response.json()["verification_status"] == "unverified"
                profile = {key: profile_response.json()[key] for key in (
                    "headline", "experience_summary", "location", "skills", "projects",
                    "target_titles", "preferences", "verification_status",
                )}
                profile["location"] = "Port Harcourt, Nigeria (test update)"
                headers = {"Origin": get_settings().frontend_origin, "X-CSRF-Token": csrf}
                updated = await client.put("/api/profile", json=profile, headers=headers)
                assert updated.status_code == 200
                assert updated.json()["location"].endswith("test update)")
                payload = {"title": "Cloud Engineer", "company": "Example Co", "source_url": "https://example.com/jobs/1", "source_name": "Manual"}
                first = await client.post("/api/jobs", json=payload, headers=headers)
                assert first.status_code == 201
                with TestingSession() as db:
                    application = Application(job_id=first.json()["id"], status="Prepared", notes="test record")
                    db.add(application)
                    db.commit()
                    assert db.query(Application).filter_by(job_id=first.json()["id"]).one().status == "Prepared"
                with TestingSession() as db:
                    assert db.query(Job).count() == 1
                duplicate = await client.post("/api/jobs", json=payload, headers=headers)
                assert duplicate.status_code == 409
                assert (await client.post("/api/auth/logout", headers=headers)).status_code == 200
                assert (await client.get("/api/profile")).status_code == 401

        async def exercise_and_close():
            try:
                await exercise_api()
            finally:
                # MCP async contexts must be closed in the event loop where they were opened.
                await rea_service.close()

        asyncio.run(exercise_and_close())
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()
