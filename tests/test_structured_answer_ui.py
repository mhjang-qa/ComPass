from pathlib import Path


def test_structured_answer_frontend_supports_cards_and_expand() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")
    style = Path("static/style.css").read_text(encoding="utf-8")

    assert 'payload.answer_type === "faculty"' in script
    assert "cards.slice(limit)" in script
    assert "전체 교수진 보기" in script
    assert "간단히 보기" in script
    assert "scrollIntoView" in script
    assert ".faculty-card" in style
    assert ".is-collapsed-item" in style


def test_chat_scroll_area_reserves_safe_bottom_space() -> None:
    style = Path("static/style.css").read_text(encoding="utf-8")

    assert "--composer-height" in style
    assert "--quick-menu-height" in style
    assert "--bottom-nav-height" in style
    assert "--safe-bottom: env(safe-area-inset-bottom" in style
    assert "scroll-padding-bottom" in style
    assert "var(--safe-bottom)" in style


def test_mobile_opens_fullscreen_and_handles_keyboard() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")
    style = Path("static/style.css").read_text(encoding="utf-8")
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert "window.innerWidth <= 768" in script
    assert 'window.matchMedia("(pointer: coarse)")' in script
    assert "visualViewport" in script
    assert "setWindowMode(isMobileDevice())" in script
    assert ".app-shell.mobile-fullscreen" in style
    assert "height: var(--app-height, 100dvh)" in style
    assert "body.keyboard-open" in style
    assert "궁금한 컴퓨터과학과 정보를 질문해보세요" in html


def test_actions_support_expand_link_and_confirm_llm() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")

    assert 'item.type === "expand"' in script
    assert 'action.type === "link"' in script
    assert 'action.type === "confirm_llm"' in script
    assert "confirm-actions" in script
