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


def test_mobile_admin_scroll_and_two_line_subtitle_contract() -> None:
    style = (main.BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")
    script = (main.BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
    html = (main.BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    config = (main.BASE_DIR / "static" / "config.js").read_text(encoding="utf-8")

    assert 'appShell.classList.toggle("admin-mode"' in script
    assert ".mobile-fullscreen.admin-mode .panel.active" in style
    assert "overflow-y: auto" in style
    assert "-webkit-overflow-scrolling: touch" in style
    assert "calc(120px + env(safe-area-inset-bottom" in style
    assert "white-space: nowrap" in style
    assert "logout-short" in html
    assert "data-app-subtitle-line1" in html
    assert "data-app-subtitle-line2" in html
    assert "Computer Science X Compass" in config
    legacy_subtitle = "Computer Science + Compass" + " · 학생들의 길잡이"
    assert legacy_subtitle not in html + config + style + script
