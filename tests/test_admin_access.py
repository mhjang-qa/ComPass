import pytest
from fastapi import HTTPException

import main


def test_admin_login_accepts_configured_password(monkeypatch) -> None:
    monkeypatch.setattr(main.config, "ADMIN_PASSWORD", "safe-test-password")

    result = main.admin_login(main.AdminLoginRequest(password="safe-test-password"))

    assert result == {"ok": True}


def test_admin_login_rejects_wrong_password(monkeypatch) -> None:
    monkeypatch.setattr(main.config, "ADMIN_PASSWORD", "safe-test-password")

    with pytest.raises(HTTPException) as exc:
        main.admin_login(main.AdminLoginRequest(password="wrong-password"))

    assert exc.value.status_code == 401
    assert exc.value.detail == "관리자 비밀번호가 올바르지 않습니다."


def test_admin_login_is_blocked_when_password_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(main.config, "ADMIN_PASSWORD", "")

    with pytest.raises(HTTPException) as exc:
        main.admin_login(main.AdminLoginRequest(password="anything"))

    assert exc.value.status_code == 503


def test_frontend_has_kst_formatter_and_protected_admin_tabs() -> None:
    source = (main.BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
    time_source = (main.BASE_DIR / "static" / "time.js").read_text(encoding="utf-8")

    assert 'new Intl.DateTimeFormat("en-CA"' in time_source
    assert '"Asia/Seoul"' in time_source
    assert "window.ComPassTime" in source
    assert 'sessionStorage.setItem("admin_auth", "true")' in source
    assert 'sessionStorage.removeItem("admin_auth")' in source
    assert 'new Set(["crawl", "index", "stats"])' in source
