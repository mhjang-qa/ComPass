from pathlib import Path


def test_structured_answer_frontend_supports_cards_and_expand() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")
    style = Path("static/style.css").read_text(encoding="utf-8")

    assert "faculty: renderFacultyList" in script
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


def test_chat_pending_state_disables_duplicate_inputs() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")
    style = Path("static/style.css").read_text(encoding="utf-8")

    assert "let isChatPending = false" in script
    assert "function setChatPending(pending)" in script
    assert "if (isChatPending) return;" in script
    assert "답변을 준비하고 있습니다..." in script
    assert "$$(\"[data-question]\").forEach((button) => {" in script
    assert ".app-shell.is-pending .quick-actions button" in style
    assert ".composer textarea:disabled" in style


def test_welcome_message_uses_bot_bubble_and_language_popup_is_disabled() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")
    style = Path("static/style.css").read_text(encoding="utf-8")
    html = Path("templates/index.html").read_text(encoding="utf-8")

    assert "languageGate" not in html
    assert "Language Selection" not in html
    assert "data-select-language" not in html + script
    assert "chat-intro" not in html + script
    assert 'const DEFAULT_LANG = "ko"' in script
    assert 'setLanguage(localStorage.getItem(LANGUAGE_KEY) || DEFAULT_LANG)' in script
    assert 'addMessage("bot", t("introMessage")' in script
    assert 'row.classList.add("with-avatar", "welcome-message", "message-row", "assistant")' in script
    assert 'bubble?.classList.add("message-bubble", "assistant-bubble")' in script
    assert ".welcome-message .assistant-bubble" in style
    assert ".message-avatar" in style


def test_answer_type_renderers_and_per_item_links_are_present() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")
    style = Path("static/style.css").read_text(encoding="utf-8")

    for renderer in (
        "renderNoticeList",
        "renderFacultyList",
        "renderCourseTable",
        "renderScheduleList",
        "renderRecommendation",
        "renderCourseDetail",
        "renderCourseDifficulty",
        "renderGenericCards",
        "renderTextAnswer",
    ):
        assert f"function {renderer}" in script
    assert "item.source_url || item.fallback_url || fallbackUrl" in script
    assert "subjects.slice(0, 3)" in script
    assert 'link.rel = "noopener noreferrer"' in script
    assert ".answer-link-button" in style
    assert "min-height: 40px" in style
    assert 'yes.dataset.buttonLabelKo = confirmAction?.label || I18N.ko.buttons.useAiHelper' in script


def test_response_buttons_are_localized_on_language_change() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")

    assert "buttons: {" in script
    assert 'courseInfo: "View Course Information"' in script
    assert 'schedule: "View Schedule"' in script
    assert 'useAiHelper: "Use AI Helper"' in script
    assert 'endSearch: "End Search"' in script
    assert '"인공지능": "AI"' in script
    assert "function translateButtonLabel(label = \"\")" in script
    assert "function updateRenderedButtonLabels()" in script
    assert 'link.dataset.actionLabelKo = action.label || buttonLabel("link")' in script
    assert 'link.textContent = actionText(link.dataset.actionLabelKo)' in script
    assert "updateRenderedButtonLabels();" in script
