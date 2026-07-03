import json
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
    assert "function enforceResponsiveWindowMode()" in script
    assert 'document.body.classList.toggle("chat-mobile-open"' in script
    assert ".app-shell.mobile-fullscreen" in style
    assert "body.chat-mobile-open" in style
    assert "width: 100% !important" in style
    assert "max-width: 100% !important" in style
    assert "height: var(--app-height, 100dvh)" in style
    assert "body.keyboard-open" in style
    assert "궁금한 컴퓨터과학과 정보를 질문해보세요" in html


def test_actions_support_expand_link_and_confirm_llm() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")

    assert 'item.type === "expand"' in script
    assert 'action.type === "link"' in script
    assert 'action.type === "confirm_llm"' in script
    assert "confirm-actions" in script
    assert "inlineTarget" in script
    assert "llm-inline-status" in script
    assert "friendlyLlmErrorMessage" in script
    assert "[CHAT_REQUEST]" in script
    assert "[CHAT_RESPONSE]" in script
    assert "[REQUEST]" in script
    assert "window.chatSubmitting" in script
    assert "window.__chatBound" in script
    assert "row.dataset.llmPending" in script
    assert "markResponseRendered" in script
    assert 'result.answer_type === "llm_fallback"' in script


def test_chat_pending_state_disables_duplicate_inputs() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")
    style = Path("static/style.css").read_text(encoding="utf-8")

    assert "let isChatPending = false" in script
    assert "function setChatPending(pending)" in script
    assert "window.chatSubmitting || isChatPending" in script
    assert "답변을 준비하고 있습니다..." in script
    assert "$$(\"[data-question]\").forEach((button) => {" in script
    assert ".app-shell.is-pending .quick-actions button" in style
    assert ".composer textarea:disabled" in style
    assert "const TYPING_SPEED_MS = 12" in script
    assert "const MAX_TYPING_MS = 2500" in script
    assert "function shouldAnimateRagAnswer(payload = {})" in script
    assert 'payload.mode === "DB검색"' in script
    assert "async function addTypedRagMessage" in script
    assert "await typeIntoElement(lead, text)" in script


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
    assert "ensureIntroMessage();" in script
    assert 'messages.querySelectorAll(\'[data-intro-message="true"]\')' in script
    assert 'document.querySelector(".welcome-card")' in script
    assert "return null;" in script
    assert 'bubble?.classList.add("message-bubble", "assistant-bubble")' in script
    assert ".welcome-message .assistant-bubble" in style
    assert ".welcome-card" in style
    assert "display: flex;\n  flex-direction: column;" in style
    assert "flex: 0 0 auto" in style
    assert ".message.bot.with-avatar.welcome-message .bubble" in style
    assert 'class="chat-bottom"' in html
    assert ".chat-bottom" in style
    assert "border-top: 1px solid var(--line)" in style
    assert "[\".chat-bottom\"].forEach" in script
    assert 'data-i18n="brandName"' in html
    assert 'data-i18n="brandSubtitle"' in html
    assert 'data-i18n="brandTagline"' in html
    assert 'data-i18n="welcomeTitle"' in html
    assert 'data-i18n="welcomeSubtitle"' in html
    assert 'brandName: "ComPass"' in script
    assert 'welcomeTitle: "How can I help you?"' in script
    assert "function safeText(key, fallback = \"\")" in script
    assert ".message-avatar" in style
    assert "quick-action-btn" in script + html
    assert "flex: 0 0 auto" in style
    assert "height: 34px" in style


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
    assert "function expandButtonLabel(answerType, totalCount, fallbackLabel = \"\")" in script
    assert "function updateRenderedButtonLabels()" in script
    assert "View All Faculty (${facultyExpandMatch[1]})" in script
    assert "button.dataset.expandButton" in script
    assert "node.dataset.expanded" in script
    assert 'link.dataset.actionLabelKo = action.label || buttonLabel("link")' in script
    assert 'link.textContent = actionText(link.dataset.actionLabelKo)' in script
    assert "updateRenderedButtonLabels();" in script


def test_english_mode_localizes_card_internal_labels() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")

    for expected in (
        'curriculumTitle: "Computer Science curriculum information."',
        'courseName: "Course Name"',
        'category: "Category"',
        'description: "Description"',
        'facultyTitle: "Faculty information."',
        'researchArea: "Research Area"',
        'date: "Date"',
        'period: "Period"',
        '"컴퓨터의이해": "Introduction to Computer Science"',
        '"파이썬프로그래밍기초": "Basic Python Programming"',
        '"자료구조": "Data Structures"',
        '"컴퓨터통신망특론": "Advanced Computer Networks"',
        '"고급정보과학특론": "Advanced Information Science"',
        '"컴퓨터과학개론": "Introduction to Computer Science"',
        '"머신러닝특론": "Advanced Machine Learning"',
        '"알고리즘특론": "Advanced Algorithms"',
        '"인공지능": "AI"',
    ):
        assert expected in script
    assert "function cardText(key)" in script
    assert "function translateCardText(value)" in script
    assert "const COURSE_TRANSLATIONS_EN = {" in script
    assert 'description: "Foundational course for computer science majors"' in script
    assert "function displayCourseDescription(item = {})" in script
    assert 'return containsKorean(description) ? "" : description' in script
    assert "function displayFacultyCourseName(name)" in script
    assert 'console.warn("[i18n] Missing faculty course translation", text)' in script
    assert 'View All Faculty (${totalCount})' in script
    assert 'appendField(card, cardText("date"), formatDateOnly(item.date))' in script
    assert 'appendField(card, cardText("period"), formatSchedulePeriod(item))' in script


def test_cold_start_index_gate_and_retry_overlay_are_present() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")
    style = Path("static/style.css").read_text(encoding="utf-8")

    for expected in (
        "let startupGateActive = true",
        "let pendingChatRequest = null",
        "function fetchWithTimeout",
        "function waitForServerReady",
        "function startServerRecovery",
        "function retryPendingChatRequest",
        'jsonFetch("/api/index/status", { cache: "no-store", timeoutMs: SERVER_WAKE_TIMEOUT_MS })',
        'status === "index_loading"',
        'status === "server_waking"',
        "skipUserBubble: true",
        'indexPreparingTitle: "Preparing ComPass."',
        'serverPreparingTitle: "Preparing ComPass again."',
    ):
        assert expected in script

    for expected in (
        ".app-blur",
        ".coldstart-overlay",
        ".coldstart-modal",
        ".coldstart-logo-fallback",
        "@keyframes compass-spin",
    ):
        assert expected in style


def test_github_pages_static_resources_exist_and_use_relative_paths() -> None:
    html = Path("index.html").read_text(encoding="utf-8")
    manifest = json.loads(Path("manifest.json").read_text(encoding="utf-8"))

    for resource in (
        Path("manifest.json"),
        Path("static/config.js"),
        Path("static/icons/icon.png"),
        Path("static/icons/favicon-32x32.png"),
        Path("static/icons/favicon-16x16.png"),
    ):
        assert resource.exists()
        assert resource.stat().st_size > 0

    assert 'href="./static/icons/favicon-32x32.png"' in html
    assert 'href="./static/icons/favicon-16x16.png"' in html
    assert 'href="./static/icons/icon.png"' in html
    assert 'href="./manifest.json"' in html
    assert 'src="./static/config.js"' in html
    assert 'href="/static/' not in html
    assert 'href="/manifest.json"' not in html

    assert manifest["start_url"] == "./"
    assert manifest["scope"] == "./"
    assert {icon["src"] for icon in manifest["icons"]} == {
        "./static/icons/icon.png",
        "./static/icons/favicon-32x32.png",
        "./static/icons/favicon-16x16.png",
    }
