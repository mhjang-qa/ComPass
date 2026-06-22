from pathlib import Path


def test_structured_answer_frontend_supports_cards_and_expand() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")
    style = Path("static/style.css").read_text(encoding="utf-8")

    assert 'payload.answer_type === "faculty"' in script
    assert "cards.slice(3)" in script
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
