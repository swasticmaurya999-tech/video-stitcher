"""Smoke tests for the API error paths (no ffmpeg/ML needed).

We avoid submitting a *valid* job (that would require ffmpeg); we exercise health + validation.
"""
import io

from fastapi.testclient import TestClient

from app.main import app


def test_health():
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"


def test_upload_rejects_bad_extension():
    with TestClient(app) as c:
        r = c.post("/api/jobs", files=[("files", ("evil.exe", io.BytesIO(b"x"), "application/octet-stream"))])
        assert r.status_code == 415
        assert r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_get_unknown_job_404():
    with TestClient(app) as c:
        r = c.get("/api/jobs/does-not-exist")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_invalid_duration_rejected():
    with TestClient(app) as c:
        r = c.post(
            "/api/jobs",
            data={"target_duration": "500"},
            files=[("files", ("a.mp4", io.BytesIO(b"\x00\x00"), "video/mp4"))],
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_DURATION"
