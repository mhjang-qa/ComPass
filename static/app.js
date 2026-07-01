const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const messages = $("#messages");
const appShell = $("#appShell");
const chatLauncher = $("#chatLauncher");
const ADMIN_TABS = new Set(["system", "crawl", "intents", "index", "stats", "quick", "icons"]);
const APP_CONFIG = window.COMPASS_CONFIG || {};
const APP_DEFAULTS = {
  appName: "ComPass",
  appSubtitleLine1: "Computer Science X Compass",
};
const STATIC_BASE = (() => {
  if (window.COMPASS_STATIC_BASE) return window.COMPASS_STATIC_BASE.replace(/\/$/, "");
  if (window.location.hostname.endsWith("github.io")) return "/ComPass/static";
  return "/static";
})();
let pendingAdminTab = "";
let adminPassword = "";
const mobilePointer = window.matchMedia("(pointer: coarse)");
const pendingRequests = new Map();
const pendingByQuestion = new Map();
let isChatPending = false;
let indexReady = false;
let startupGateActive = true;
let coldStartActive = false;
let coldStartFailed = false;
let pendingChatRequest = null;
let serverRecoveryPolling = false;
const LANGUAGE_KEY = "compass_language";
const DEFAULT_LANG = "ko";
let currentLanguage = localStorage.getItem(LANGUAGE_KEY) || DEFAULT_LANG;
let DEFAULT_CHAT_PLACEHOLDER = "궁금한 컴퓨터과학과 정보를 질문해보세요";
let PENDING_CHAT_PLACEHOLDER = "답변을 준비하고 있습니다...";
const QUICK_QUESTIONS_KEY = "COMPASS_QUICK_QUESTIONS";
const ICON_CONFIG_KEY = "COMPASS_ICON_CONFIG";
const INDEX_LOADING_MAX_RETRIES = 3;
const INDEX_LOADING_DEFAULT_DELAY_MS = 1500;
const SERVER_WAKE_TIMEOUT_MS = 10000;
const SERVER_DELAY_NOTICE_ATTEMPTS = 20;
const SERVER_READY_INTERVAL_MS = 2000;
const DEFAULT_QUICK_QUESTIONS = [
  { id: 1, label: "교육과정", message: "컴퓨터과학과 교육과정을 알려줘", intent: "curriculum", enabled: true, sortOrder: 1 },
  { id: 2, label: "교수진", message: "컴퓨터과학과 교수진을 알려줘", intent: "faculty", enabled: true, sortOrder: 2 },
  { id: 3, label: "최근 공지", message: "컴퓨터과학과 최근 공지를 알려줘", intent: "notice", enabled: true, sortOrder: 3 },
  { id: 4, label: "학과 일정", message: "컴퓨터과학과 학과 일정을 알려줘", intent: "schedule", enabled: true, sortOrder: 4 },
];
const QUICK_QUESTIONS_BY_LANGUAGE = {
  ko: DEFAULT_QUICK_QUESTIONS,
  en: [
    { id: 1, label: "Curriculum", message: "Show me curriculum", intent: "curriculum", enabled: true, sortOrder: 1 },
    { id: 2, label: "Faculty", message: "Who are the professors?", intent: "faculty", enabled: true, sortOrder: 2 },
    { id: 3, label: "Latest Notice", message: "Latest notice", intent: "notice", enabled: true, sortOrder: 3 },
    { id: 4, label: "Schedule", message: "Department schedule", intent: "schedule", enabled: true, sortOrder: 4 },
  ],
};
const I18N = {
  ko: {
    brandName: "ComPass",
    brandSubtitle: "Computer Science X Compass",
    brandTagline: "· 학생들의 길잡이",
    welcomeTitle: "안녕하세요, ComPass입니다.",
    welcomeSubtitle: "공식 정보를 학생이 이해하기 쉽게 정리해 안내합니다.",
    welcomeMessage: "안녕하세요, ComPass입니다.\n공식 정보를 학생이 이해하기 쉽게 정리해 안내합니다.",
    placeholder: "궁금한 컴퓨터과학과 정보를 질문해보세요",
    pending: "답변을 준비하고 있습니다...",
    indexPreparingPlaceholder: "공식 정보 검색 인덱스를 준비 중입니다...",
    serverPreparingPlaceholder: "ComPass 서버를 다시 준비 중입니다...",
    indexPreparingTitle: "ComPass를 준비 중입니다.",
    indexPreparingLine1: "공식 정보 검색 인덱스를 불러오는 중입니다.",
    indexPreparingLine2: "잠시만 기다려 주세요.",
    serverPreparingTitle: "ComPass를 다시 준비 중입니다.",
    serverPreparingLine1: "서버가 절전 상태에서 깨어나는 중입니다.",
    serverPreparingLine2: "잠시만 기다려 주세요.",
    serverPreparingLine3: "준비가 완료되면 방금 질문을 자동으로 다시 전송합니다.",
    serverDelayedTitle: "ComPass 서버 준비가 지연되고 있습니다.",
    serverDelayedLine1: "계속 확인 중입니다. 준비가 완료되면 자동으로 이동합니다.",
    close: "닫기",
    waiting: "공식 데이터를 검색하고 있습니다",
    waitingSub: "잠시만 기다려주세요.",
    send: "전송",
    pendingButton: "대기",
    subtitleLine2: "· 학생들의 길잡이",
    introMessage: "안녕하세요, ComPass입니다.\n공식 정보를 학생이 이해하기 쉽게 정리해 안내합니다.",
    tab_chat: "챗봇",
    tab_admin: "관리자 페이지",
    incomplete: "답변이 완전히 생성되지 않았습니다. 다시 시도해 주세요.",
    retry: "답변 다시 생성",
    retryServer: "다시 시도",
    confirmNo: "검색 종료",
    buttons: {
      curriculumMore: "교육과정 더보기",
      noticeMore: "공지 더보기",
      schedule: "학과 일정 바로가기",
      faculty: "교수진 바로가기",
      facultyPage: "교수진 페이지 바로가기",
      facultyHomepage: "교수 홈페이지 바로가기",
      courseInfo: "교과목 안내 바로가기",
      official: "공식 홈페이지 바로가기",
      officialPage: "공식 페이지 바로가기",
      notices: "공지사항 바로가기",
      notice: "공지 바로가기",
      useAiHelper: "LLM 보조 답변 사용",
      endSearch: "검색 종료",
      details: "자세히 보기",
      material: "자료 확인하기",
      pdf: "PDF 보기",
      original: "원문 보기",
      link: "바로가기",
    },
    cards: {
      curriculumTitle: "컴퓨터과학과 교육과정 안내입니다.",
      curriculumDesc: "공식 학과 페이지의 교육과정 메뉴에서 학년별 과목과 이수 흐름을 확인할 수 있습니다.",
      courseName: "과목명",
      category: "구분",
      description: "특징",
      gradeSemester: "학년/학기",
      facultyTitle: "교수진 안내입니다.",
      facultyDesc: "공식 학과 페이지에서 교수진 프로필과 연구 분야를 확인할 수 있습니다.",
      position: "직위",
      researchArea: "연구 분야",
      email: "이메일",
      office: "연구실",
      phone: "연락처",
      subjects: "담당 과목",
      date: "등록일",
      period: "기간",
      summary: "요약",
      details: "설명",
      swipeHint: "← 좌우로 밀어서 전체 내용을 확인할 수 있습니다.",
      compactView: "간단히 보기",
    },
  },
  en: {
    brandName: "ComPass",
    brandSubtitle: "Computer Science X Compass",
    brandTagline: "A guide for students",
    welcomeTitle: "How can I help you?",
    welcomeSubtitle: "I can guide you through Computer Science department information.",
    welcomeMessage: "Hello, this is ComPass.\nI summarize official information in a way students can easily understand.",
    placeholder: "Ask about CS department info",
    pending: "Preparing the answer...",
    indexPreparingPlaceholder: "Preparing official search index...",
    serverPreparingPlaceholder: "Preparing ComPass server again...",
    indexPreparingTitle: "Preparing ComPass.",
    indexPreparingLine1: "Loading the official search index.",
    indexPreparingLine2: "Please wait a moment.",
    serverPreparingTitle: "Preparing ComPass again.",
    serverPreparingLine1: "The server is waking up from sleep mode.",
    serverPreparingLine2: "Please wait a moment.",
    serverPreparingLine3: "Your last question will be sent again automatically.",
    serverDelayedTitle: "ComPass is taking longer than expected.",
    serverDelayedLine1: "I am still checking. The chat will open automatically when ready.",
    close: "Close",
    waiting: "Searching official data",
    waitingSub: "Please wait a moment.",
    send: "Send",
    pendingButton: "Wait",
    subtitleLine2: "A guide for students",
    introMessage: "Hello, this is ComPass.\nI summarize official information in a way students can easily understand.",
    tab_chat: "Chat",
    tab_admin: "Admin",
    incomplete: "The answer was not completed. Please try again.",
    retry: "Regenerate answer",
    retryServer: "Retry",
    confirmNo: "End search",
    buttons: {
      curriculumMore: "View Curriculum",
      noticeMore: "View More Notices",
      schedule: "View Schedule",
      faculty: "View Faculty",
      facultyPage: "View Faculty",
      facultyHomepage: "Visit Faculty Homepage",
      courseInfo: "View Course Information",
      official: "Visit Official Website",
      officialPage: "Visit Official Website",
      notices: "View Notices",
      notice: "View Notice",
      useAiHelper: "Use AI Helper",
      endSearch: "End Search",
      details: "View Details",
      material: "View Material",
      pdf: "View PDF",
      original: "View Original",
      link: "Open Link",
    },
    cards: {
      curriculumTitle: "Computer Science curriculum information.",
      curriculumDesc: "You can check courses by year and study flow in the Curriculum menu on the official department page.",
      courseName: "Course Name",
      category: "Category",
      description: "Description",
      gradeSemester: "Year/Semester",
      facultyTitle: "Faculty information.",
      facultyDesc: "You can check professor profiles and research areas on the official department page.",
      position: "Position",
      researchArea: "Research Area",
      email: "Email",
      office: "Office",
      phone: "Phone",
      subjects: "Courses",
      date: "Date",
      period: "Period",
      summary: "Summary",
      details: "Description",
      swipeHint: "Swipe horizontally to view the full table.",
      compactView: "Show Less",
    },
  },
};
const COURSE_LABEL_TRANSLATIONS = {
  en: {
    "인공지능": "AI",
    "데이터베이스시스템": "Database Systems",
    "운영체제": "Operating Systems",
    "이산수학": "Discrete Mathematics",
    "파이썬프로그래밍기초": "Basic Python Programming",
    "데이터정보처리입문": "Introduction to Data and Information Processing",
    "컴퓨터의이해": "Introduction to Computer Science",
    "유비쿼터스컴퓨팅개론": "Introduction to Ubiquitous Computing",
    "Java프로그래밍": "Java Programming",
    "HTML5웹프로그래밍": "HTML5 Web Programming",
    "자료구조": "Data Structures",
    "알고리즘": "Algorithms",
    "컴퓨터그래픽스": "Computer Graphics",
    "컴퓨터구조": "Computer Architecture",
    "소프트웨어공학": "Software Engineering",
    "정보보호": "Information Security",
    "컴퓨터보안": "Computer Security",
    "클라우드컴퓨팅": "Cloud Computing",
  },
};
const COURSE_TRANSLATIONS_EN = {
  "컴퓨터의이해": {
    name: "Introduction to Computer Science",
    description: "Introductory course in computer science",
  },
  "파이썬프로그래밍기초": {
    name: "Basic Python Programming",
    description: "Basic programming course",
  },
  "유비쿼터스컴퓨팅개론": {
    name: "Introduction to Ubiquitous Computing",
    description: "Foundational course for computer science majors",
  },
  "Java프로그래밍": {
    name: "Java Programming",
    description: "Object-oriented programming course",
  },
  "HTML5웹프로그래밍": {
    name: "HTML5 Web Programming",
    description: "Web application development course",
  },
  "이산수학": {
    name: "Discrete Mathematics",
    description: "Foundational mathematics for computer science",
  },
  "자료구조": {
    name: "Data Structures",
    description: "Data organization and processing",
  },
  "운영체제": {
    name: "Operating Systems",
    description: "Operating system principles and architecture",
  },
  "데이터베이스시스템": {
    name: "Database Systems",
    description: "Database design and management",
  },
  "알고리즘": {
    name: "Algorithms",
    description: "Algorithm design and analysis",
  },
  "인공지능": {
    name: "Artificial Intelligence",
    description: "AI theories and applications",
  },
  "컴퓨터그래픽스": {
    name: "Computer Graphics",
    description: "Computer graphics fundamentals",
  },
};
const CARD_TEXT_TRANSLATIONS = {
  en: {
    "전공": "Major",
    "전공선택": "Major Elective",
    "전공필수": "Required Major",
    "교양": "General Education",
    "일반선택": "General Elective",
    "1학년": "Year 1",
    "2학년": "Year 2",
    "3학년": "Year 3",
    "4학년": "Year 4",
    "교수": "Professor",
    "부교수": "Associate Professor",
    "조교수": "Assistant Professor",
    "컴퓨터통신망특론": "Advanced Computer Networks",
    "고급정보과학특론": "Advanced Information Science",
    "컴퓨터과학개론": "Introduction to Computer Science",
    "머신러닝특론": "Advanced Machine Learning",
    "알고리즘특론": "Advanced Algorithms",
    "정보통신망": "Information and Communication Networks",
    "컴퓨터의이해": "Introduction to Computer Science",
    "이산수학": "Discrete Mathematics",
    "자료구조": "Data Structures",
    "알고리즘": "Algorithms",
    "운영체제": "Operating Systems",
    "데이터베이스시스템": "Database Systems",
    "인공지능": "Artificial Intelligence",
    "컴퓨터그래픽스": "Computer Graphics",
    "Java프로그래밍": "Java Programming",
    "HTML5웹프로그래밍": "HTML5 Web Programming",
    "파이썬프로그래밍기초": "Basic Python Programming",
    "유비쿼터스컴퓨팅개론": "Introduction to Ubiquitous Computing",
    "컴퓨터과학 입문": "Introductory course in computer science",
    "프로그래밍 기초": "Basic programming course",
    "객체지향 프로그래밍": "Object-oriented programming",
    "전공 수학 기초": "Foundational mathematics for computer science",
    "자료구조 기초": "Foundations of data structures",
    "운영체제 기초": "Foundations of operating systems",
    "데이터 관리 기초": "Foundations of data management",
    "AI 기초": "Foundations of AI",
    "그래픽스 기초": "Foundations of computer graphics",
    "보안 기초": "Foundations of security",
    "클라우드 기술 이해": "Understanding cloud technologies",
    "학과 공식 일정": "Official department schedule",
    "학과 일정 관련 공식 안내입니다.": "Official department schedule information.",
  },
};
const DEFAULT_ICONS = {
  externalIcon: `${STATIC_BASE}/icons/chatbot-external.png`,
  internalIcon: `${STATIC_BASE}/icons/chatbot-internal.png`,
  faviconIcon: `${STATIC_BASE}/icons/favicon-32x32.png`,
};

function getSessionId() {
  let sessionId = sessionStorage.getItem("compass_session_id");
  if (!sessionId) {
    sessionId = crypto.randomUUID ? crypto.randomUUID() : `session-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    sessionStorage.setItem("compass_session_id", sessionId);
  }
  return sessionId;
}

const SESSION_ID = getSessionId();

function newRequestId() {
  return crypto.randomUUID ? crypto.randomUUID() : `request-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function setChatPending(pending) {
  isChatPending = Boolean(pending);
  const blocked = isChatPending || !indexReady || startupGateActive || coldStartActive;
  appShell.classList.toggle("is-pending", blocked);
  const input = $("#question");
  const sendButton = $("#sendButton");
  if (input) {
    input.disabled = blocked;
    input.placeholder = coldStartActive
      ? t("serverPreparingPlaceholder")
      : (!indexReady || startupGateActive)
        ? t("indexPreparingPlaceholder")
        : isChatPending
          ? PENDING_CHAT_PLACEHOLDER
          : DEFAULT_CHAT_PLACEHOLDER;
  }
  if (sendButton) {
    sendButton.disabled = blocked;
    sendButton.textContent = isChatPending ? t("pendingButton") : t("send");
  }
  $$("[data-question]").forEach((button) => {
    button.disabled = blocked;
  });
  $$(".confirm-actions button").forEach((button) => {
    button.disabled = blocked;
  });
  const languageSelect = $("#languageSelect");
  if (languageSelect) languageSelect.disabled = coldStartActive;
}

function t(key) {
  return (I18N[currentLanguage || "ko"] || I18N.ko)[key] || I18N.ko[key] || key;
}

function safeText(key, fallback = "") {
  const value = (I18N[currentLanguage || "ko"] || I18N.ko)[key] || I18N.ko[key];
  return value || fallback || key;
}

function buttonLabel(key) {
  const language = currentLanguage || "ko";
  return (I18N[language]?.buttons || I18N.ko.buttons)[key] || I18N.ko.buttons[key] || key;
}

function cardText(key) {
  const language = currentLanguage || "ko";
  return (I18N[language]?.cards || I18N.ko.cards)[key] || I18N.ko.cards[key] || key;
}

function translateCardText(value) {
  const text = String(value || "");
  if ((currentLanguage || "ko") !== "en" || !text) return text;
  return CARD_TEXT_TRANSLATIONS.en[text] || COURSE_LABEL_TRANSLATIONS.en[text] || text;
}

function containsKorean(value) {
  return /[가-힣]/.test(String(value || ""));
}

function courseTranslation(item = {}) {
  const name = String(item.course_name || item.title || item.name || "").trim();
  return COURSE_TRANSLATIONS_EN[name] || null;
}

function displayCourseName(item = {}) {
  const name = String(item.course_name || item.title || item.name || "").trim();
  if ((currentLanguage || "ko") !== "en") return name;
  return courseTranslation(item)?.name || COURSE_LABEL_TRANSLATIONS.en[name] || CARD_TEXT_TRANSLATIONS.en[name] || name;
}

function displayCourseDescription(item = {}) {
  const description = item.feature_summary || item.feature || item.description || item.overview || "";
  if ((currentLanguage || "ko") !== "en") return description;
  const translated = courseTranslation(item)?.description;
  if (translated) return translated;
  return containsKorean(description) ? "" : description;
}

function displayFacultyCourseName(name) {
  const text = String(name || "").trim();
  if (!text) return "";
  if ((currentLanguage || "ko") !== "en") return text;
  const translated = CARD_TEXT_TRANSLATIONS.en[text] || COURSE_LABEL_TRANSLATIONS.en[text] || COURSE_TRANSLATIONS_EN[text]?.name || "";
  if (!translated && containsKorean(text)) {
    console.warn("[i18n] Missing faculty course translation", text);
  }
  return translated || (containsKorean(text) ? "" : text);
}

function translateButtonLabel(label = "") {
  const raw = String(label || "").replace(/\s*↗\s*$/, "").trim();
  const englishToKo = {
    [I18N.en.buttons.curriculumMore]: I18N.ko.buttons.curriculumMore,
    "View More Curriculum": I18N.ko.buttons.curriculumMore,
    [I18N.en.buttons.noticeMore]: I18N.ko.buttons.noticeMore,
    [I18N.en.buttons.schedule]: I18N.ko.buttons.schedule,
    "View Department Schedule": I18N.ko.buttons.schedule,
    [I18N.en.buttons.faculty]: I18N.ko.buttons.facultyPage,
    [I18N.en.buttons.facultyPage]: I18N.ko.buttons.facultyPage,
    [I18N.en.buttons.facultyHomepage]: I18N.ko.buttons.facultyHomepage,
    [I18N.en.buttons.courseInfo]: I18N.ko.buttons.courseInfo,
    [I18N.en.buttons.official]: I18N.ko.buttons.officialPage,
    [I18N.en.buttons.officialPage]: I18N.ko.buttons.officialPage,
    [I18N.en.buttons.notices]: I18N.ko.buttons.notices,
    [I18N.en.buttons.notice]: I18N.ko.buttons.notice,
    [I18N.en.buttons.useAiHelper]: I18N.ko.buttons.useAiHelper,
    [I18N.en.buttons.endSearch]: I18N.ko.buttons.endSearch,
    [I18N.en.buttons.details]: I18N.ko.buttons.details,
    [I18N.en.buttons.material]: I18N.ko.buttons.material,
    [I18N.en.buttons.pdf]: I18N.ko.buttons.pdf,
    [I18N.en.buttons.original]: I18N.ko.buttons.original,
    [I18N.en.buttons.link]: I18N.ko.buttons.link,
    "View AI Course": "인공지능 과목 바로가기",
  };
  if (!raw) return buttonLabel("link");
  if (currentLanguage !== "en") return englishToKo[raw] || raw;
  const exact = {
    [I18N.ko.buttons.curriculumMore]: buttonLabel("curriculumMore"),
    "전체 교육과정 바로가기": buttonLabel("curriculumMore"),
    "교육과정 바로가기": buttonLabel("curriculumMore"),
    "교육과정 확인하기": buttonLabel("curriculumMore"),
    [I18N.ko.buttons.noticeMore]: buttonLabel("noticeMore"),
    "전체 공지 바로가기": buttonLabel("noticeMore"),
    [I18N.ko.buttons.schedule]: buttonLabel("schedule"),
    [I18N.ko.buttons.faculty]: buttonLabel("faculty"),
    [I18N.ko.buttons.facultyPage]: buttonLabel("facultyPage"),
    [I18N.ko.buttons.facultyHomepage]: buttonLabel("facultyHomepage"),
    [I18N.ko.buttons.courseInfo]: buttonLabel("courseInfo"),
    [I18N.ko.buttons.official]: buttonLabel("official"),
    [I18N.ko.buttons.officialPage]: buttonLabel("officialPage"),
    [I18N.ko.buttons.notices]: buttonLabel("notices"),
    [I18N.ko.buttons.notice]: buttonLabel("notice"),
    [I18N.ko.buttons.useAiHelper]: buttonLabel("useAiHelper"),
    "AI Helper 사용": buttonLabel("useAiHelper"),
    [I18N.ko.buttons.endSearch]: buttonLabel("endSearch"),
    [I18N.ko.buttons.details]: buttonLabel("details"),
    [I18N.ko.buttons.material]: buttonLabel("material"),
    [I18N.ko.buttons.pdf]: buttonLabel("pdf"),
    [I18N.ko.buttons.original]: buttonLabel("original"),
    [I18N.ko.buttons.link]: buttonLabel("link"),
  };
  if (exact[raw]) return exact[raw];
  const courseMatch = raw.match(/^(.+?)\s*과목\s*바로가기$/);
  if (courseMatch) {
    const courseName = courseMatch[1].trim();
    const translatedCourse = COURSE_LABEL_TRANSLATIONS.en[courseName] || courseName;
    return `View ${translatedCourse} Course`;
  }
  return raw;
}

function actionText(label) {
  return `${translateButtonLabel(label)} ↗`;
}

function updateRenderedButtonLabels() {
  $$("[data-action-label-ko]").forEach((node) => {
    node.textContent = actionText(node.dataset.actionLabelKo);
  });
  $$("[data-button-label-ko]").forEach((node) => {
    node.textContent = translateButtonLabel(node.dataset.buttonLabelKo);
  });
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function fetchWithTimeout(url, options = {}, timeoutMs = SERVER_WAKE_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort("timeout"), timeoutMs);
  const externalSignal = options.signal;
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort(externalSignal.reason);
    externalSignal.addEventListener("abort", () => controller.abort(externalSignal.reason), { once: true });
  }
  return fetch(url, { ...options, signal: controller.signal })
    .catch((error) => {
      if (controller.signal.aborted && !externalSignal?.aborted) {
        const timeoutError = new Error("Request timed out while the server is preparing.");
        timeoutError.kind = "FETCH_TIMEOUT";
        throw timeoutError;
      }
      throw error;
    })
    .finally(() => window.clearTimeout(timeout));
}

function isIndexReadyPayload(data = {}) {
  return data.ready === true && Number(data.indexed || data.documents || 0) > 0;
}

function isColdStartCondition(errorOrResult = {}) {
  const status = errorOrResult.status;
  return (
    errorOrResult.kind === "FETCH_TIMEOUT"
    || errorOrResult.kind === "BACKEND_CONNECTION"
    || [502, 503, 504, 522, 523, 524].includes(Number(errorOrResult.statusCode || errorOrResult.status))
    || status === "index_loading"
    || status === "server_waking"
  );
}

function ensurePreparationOverlay() {
  let overlay = $("#coldstartOverlay");
  if (overlay) return overlay;
  overlay = document.createElement("div");
  overlay.id = "coldstartOverlay";
  overlay.className = "coldstart-overlay";
  overlay.hidden = true;
  overlay.innerHTML = `
    <div class="coldstart-modal" role="status" aria-live="polite">
      <div class="coldstart-logo-fallback">ComPass</div>
      <h2 data-coldstart-title></h2>
      <p data-coldstart-line1></p>
      <p data-coldstart-line2></p>
      <p data-coldstart-line3></p>
      <div class="coldstart-spinner" aria-hidden="true"></div>
      <div class="coldstart-actions" hidden>
        <button type="button" data-coldstart-retry></button>
        <button type="button" data-coldstart-close></button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.querySelector("[data-coldstart-retry]").addEventListener("click", () => {
    coldStartFailed = false;
    startServerRecovery();
  });
  overlay.querySelector("[data-coldstart-close]").addEventListener("click", () => {
    overlay.hidden = true;
    coldStartActive = false;
    startupGateActive = !indexReady;
    coldStartFailed = false;
    appShell.classList.remove("app-blur");
    setChatPending(false);
  });
  return overlay;
}

function showPreparationOverlay(mode = "startup", failed = false) {
  const overlay = ensurePreparationOverlay();
  const isServer = mode === "server";
  startupGateActive = !isServer && !indexReady;
  coldStartActive = isServer;
  coldStartFailed = failed;
  const titleKey = failed ? "serverDelayedTitle" : isServer ? "serverPreparingTitle" : "indexPreparingTitle";
  const line1Key = failed ? "serverDelayedLine1" : isServer ? "serverPreparingLine1" : "indexPreparingLine1";
  const line2Key = isServer && !failed ? "serverPreparingLine2" : !isServer && !failed ? "indexPreparingLine2" : "";
  const line3Key = isServer && !failed ? "serverPreparingLine3" : "";
  overlay.querySelector("[data-coldstart-title]").textContent = t(titleKey);
  overlay.querySelector("[data-coldstart-line1]").textContent = t(line1Key);
  overlay.querySelector("[data-coldstart-line2]").textContent = line2Key ? t(line2Key) : "";
  overlay.querySelector("[data-coldstart-line3]").textContent = line3Key ? t(line3Key) : "";
  overlay.querySelector(".coldstart-spinner").hidden = false;
  overlay.querySelector(".coldstart-actions").hidden = true;
  overlay.querySelector("[data-coldstart-retry]").textContent = t("retryServer");
  overlay.querySelector("[data-coldstart-close]").textContent = t("close");
  overlay.hidden = false;
  appShell.classList.add("app-blur");
  setChatPending(isChatPending);
}

function hidePreparationOverlay() {
  const overlay = ensurePreparationOverlay();
  overlay.hidden = true;
  startupGateActive = false;
  coldStartActive = false;
  coldStartFailed = false;
  appShell.classList.remove("app-blur");
  setChatPending(false);
}

async function fetchIndexStatus() {
  return jsonFetch("/api/index/status", { cache: "no-store", timeoutMs: SERVER_WAKE_TIMEOUT_MS });
}

async function waitForServerReady(mode = "startup") {
  if (mode === "startup") {
    try {
      const initialStatus = await fetchIndexStatus();
      if (isIndexReadyPayload(initialStatus)) {
        indexReady = true;
        hidePreparationOverlay();
        return true;
      }
    } catch (error) {
      // Fall through to the visible preparation overlay.
    }
  }
  showPreparationOverlay(mode);
  let delayedNoticeShown = false;
  for (let attempt = 0; ; attempt += 1) {
    try {
      await jsonFetch("/api/health", { cache: "no-store", timeoutMs: SERVER_WAKE_TIMEOUT_MS });
      const status = await fetchIndexStatus();
      if (isIndexReadyPayload(status)) {
        indexReady = true;
        hidePreparationOverlay();
        return true;
      }
    } catch (error) {
      // Keep the preparation overlay visible while Render wakes up.
    }
    if (!delayedNoticeShown && attempt >= SERVER_DELAY_NOTICE_ATTEMPTS) {
      delayedNoticeShown = true;
      showPreparationOverlay(mode, true);
    }
    await delay(SERVER_READY_INTERVAL_MS);
  }
}

function rememberPendingChatRequest(question, options = {}) {
  pendingChatRequest = {
    question,
    options: {
      allowLlm: Boolean(options.allowLlm),
      llmType: options.llmType || "",
      context: options.context || undefined,
      skipUserBubble: true,
    },
    lang: currentLanguage,
    createdAt: Date.now(),
  };
}

async function retryPendingChatRequest() {
  if (!pendingChatRequest) return;
  const request = pendingChatRequest;
  pendingChatRequest = null;
  if (request.lang && request.lang !== currentLanguage) setLanguage(request.lang);
  await sendQuestion(request.question, request.options);
}

async function startServerRecovery() {
  if (serverRecoveryPolling) return;
  serverRecoveryPolling = true;
  try {
    const ready = await waitForServerReady("server");
    if (ready) await retryPendingChatRequest();
  } finally {
    serverRecoveryPolling = false;
  }
}

function setLanguage(language) {
  currentLanguage = language === "en" ? "en" : "ko";
  localStorage.setItem(LANGUAGE_KEY, currentLanguage);
  DEFAULT_CHAT_PLACEHOLDER = t("placeholder");
  PENDING_CHAT_PLACEHOLDER = t("pending");
  document.documentElement.lang = currentLanguage;
  const select = $("#languageSelect");
  if (select) select.value = currentLanguage;
  $$("[data-i18n]").forEach((node) => {
    node.textContent = safeText(node.dataset.i18n, node.textContent);
  });
  applyAppConstants();
  updateI18nKeyedText();
  updateRenderedButtonLabels();
  const input = $("#question");
  if (input) input.placeholder = coldStartActive
    ? t("serverPreparingPlaceholder")
    : (!indexReady || startupGateActive)
      ? t("indexPreparingPlaceholder")
      : isChatPending
        ? PENDING_CHAT_PLACEHOLDER
        : DEFAULT_CHAT_PLACEHOLDER;
  if (!ensurePreparationOverlay().hidden) {
    showPreparationOverlay(coldStartActive ? "server" : "startup", coldStartFailed);
  }
  renderQuickQuestions();
}

function initializeLanguage() {
  localStorage.removeItem("compass_language_popup_seen");
  setLanguage(localStorage.getItem(LANGUAGE_KEY) || DEFAULT_LANG);
}

// 새로고침 시 인증을 반드시 다시 받는다. 비밀번호는 브라우저 저장소에 보관하지 않는다.
sessionStorage.removeItem("admin_auth");

const { formatKstDateTime } = window.ComPassTime;

function activateTab(tabName) {
  if (ADMIN_TABS.has(tabName) && !isAdminAuthenticated()) {
    openAdminLogin("crawl");
    return;
  }
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === tabName));
  $$(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === `panel-${tabName}`));
  appShell.classList.toggle("admin-mode", ADMIN_TABS.has(tabName));
}

function applyAppConstants() {
  $$("[data-app-name]").forEach((node) => {
    node.textContent = APP_CONFIG.appName || safeText("brandName", node.textContent || APP_DEFAULTS.appName);
  });
  $$("[data-app-subtitle-line1]").forEach((node) => {
    node.textContent = APP_CONFIG.appSubtitleLine1 || safeText("brandSubtitle", node.textContent || APP_DEFAULTS.appSubtitleLine1);
  });
  $$("[data-app-subtitle-line2]").forEach((node) => {
    node.textContent = safeText("brandTagline", node.textContent);
  });
}

function updateI18nKeyedText() {
  $$("[data-i18n-key]").forEach((node) => {
    node.textContent = safeText(node.dataset.i18nKey, node.textContent);
  });
}

function isMobileDevice() {
  return (
    window.innerWidth <= 768
    || mobilePointer.matches
    || /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent)
  );
}

function updateAppHeight() {
  const viewportHeight = window.visualViewport?.height || window.innerHeight;
  document.documentElement.style.setProperty("--app-height", `${viewportHeight}px`);
  document.documentElement.style.setProperty("--visual-viewport-height", `${viewportHeight}px`);
  const keyboardOpen = isMobileDevice() && viewportHeight < window.innerHeight - 120;
  document.body.classList.toggle("keyboard-open", keyboardOpen);
}

function setWindowMode(fullscreen) {
  appShell.classList.toggle("fullscreen", fullscreen);
  appShell.classList.toggle("widget-window", !fullscreen);
  appShell.classList.toggle("mobile-fullscreen", fullscreen && isMobileDevice());
  $("#toggleFullscreen").hidden = fullscreen && isMobileDevice();
  $("#toggleFullscreen").textContent = fullscreen ? "↙" : "⛶";
  $("#toggleFullscreen").setAttribute("aria-label", fullscreen ? "창 모드로 보기" : "전체 화면으로 보기");
  $("#toggleFullscreen").setAttribute("title", fullscreen ? "창 모드" : "전체 화면");
}

function openChatWindow() {
  appShell.classList.remove("is-hidden");
  setWindowMode(isMobileDevice());
  chatLauncher.classList.add("is-hidden");
  chatLauncher.setAttribute("aria-expanded", "true");
  activateTab("chat");
  updateAppHeight();
  if (!isMobileDevice()) {
    requestAnimationFrame(() => $("#question").focus({ preventScroll: true }));
  }
}

function minimizeChat() {
  appShell.classList.add("is-hidden");
  chatLauncher.classList.remove("is-hidden");
  chatLauncher.setAttribute("aria-expanded", "false");
  chatLauncher.focus();
}

function toggleFullscreen() {
  if (isMobileDevice()) {
    setWindowMode(true);
    return;
  }
  const expanding = !appShell.classList.contains("fullscreen");
  setWindowMode(expanding);
}

chatLauncher.addEventListener("click", openChatWindow);
$("#minimizeChat").addEventListener("click", minimizeChat);
$("#toggleFullscreen").addEventListener("click", toggleFullscreen);
window.addEventListener("resize", updateAppHeight);
window.addEventListener("orientationchange", updateAppHeight);
window.visualViewport?.addEventListener("resize", updateAppHeight);
window.visualViewport?.addEventListener("scroll", updateAppHeight);

function adminHeaders() {
  return { "X-Admin-Password": adminPassword };
}

function isAdminAuthenticated() {
  return sessionStorage.getItem("admin_auth") === "true" && Boolean(adminPassword);
}

function updateAdminUi() {
  const authenticated = isAdminAuthenticated();
  document.body.classList.toggle("admin-authenticated", authenticated);
  appShell.classList.toggle("admin-authenticated", authenticated);
  $("#adminLogout").hidden = !authenticated;
  if (!authenticated && ADMIN_TABS.has($(".panel.active")?.id?.replace("panel-", ""))) {
    activateTab("chat");
  }
}

function openAdminLogin(tabName) {
  pendingAdminTab = ADMIN_TABS.has(tabName) ? tabName : "system";
  $("#adminLoginError").textContent = "";
  $("#adminLoginPassword").value = "";
  $("#adminLoginModal").hidden = false;
  requestAnimationFrame(() => $("#adminLoginPassword").focus());
}

function closeAdminLogin() {
  $("#adminLoginModal").hidden = true;
  pendingAdminTab = "";
}

async function enterAdminTab(tabName) {
  if (!isAdminAuthenticated()) {
    openAdminLogin(tabName);
    return;
  }
  activateTab(tabName);
  if (tabName === "system") await loadSystemDashboard();
  if (tabName === "crawl") await loadKnowledge();
  if (tabName === "intents") renderIntentOverview();
  if (tabName === "index") await loadIndexStatus();
  if (tabName === "stats") await loadStats();
  if (tabName === "quick") renderQuickQuestionManager();
  if (tabName === "icons") renderIconManager();
}

async function jsonFetch(url, options = {}) {
  const { timeoutMs, ...fetchOptions } = options;
  let response;
  try {
    response = timeoutMs
      ? await fetchWithTimeout(url, fetchOptions, timeoutMs)
      : await fetch(url, fetchOptions);
  } catch (cause) {
    if (cause?.kind === "FETCH_TIMEOUT") throw cause;
    if (cause?.name === "AbortError") throw cause;
    const error = new Error("백엔드 서버에 연결할 수 없습니다. Render가 부팅 중인지 확인해 주세요.");
    error.kind = "BACKEND_CONNECTION";
    error.cause = cause;
    throw error;
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || `백엔드 요청 실패 (${response.status})`);
    error.kind = response.status >= 500 ? "BACKEND_SERVER" : "BACKEND_REQUEST";
    error.status = response.status;
    error.statusCode = response.status;
    throw error;
  }
  return data;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function scrollMessageIntoView(row, behavior = "smooth") {
  requestAnimationFrame(() => {
    row.scrollIntoView({ behavior, block: "end" });
  });
}

function formatDateOnly(value) {
  return value ? String(value).slice(0, 10) : "";
}

function formatSchedulePeriod(item) {
  const start = formatDateOnly(item.start_date);
  const end = formatDateOnly(item.end_date);
  if (!start) return "";
  if (!end || start === end) return start;
  return `${start.slice(5)} ~ ${end.slice(5)}`;
}

const chatChromeObserver = new ResizeObserver(() => {
  const lastMessage = messages.lastElementChild;
  if (lastMessage && document.activeElement !== $("#question")) {
    scrollMessageIntoView(lastMessage, "auto");
  }
});
[".suggestions", ".composer"].forEach((selector) => {
  const node = $(selector);
  if (node) chatChromeObserver.observe(node);
});

function appendSourceLinks(container, sources = []) {
  const unique = sources.filter(
    (source, index, all) => source?.url && all.findIndex((item) => item?.url === source.url) === index,
  );
  if (!unique.length) return;
  const sourceList = document.createElement("div");
  sourceList.className = "source-list";
  unique.forEach((source, index) => {
    const link = document.createElement("a");
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const score = source.score === undefined ? "" : ` (${source.score}점)`;
    link.textContent = `출처 ${index + 1}. ${source.title || "공식 페이지"}${score}`;
    sourceList.appendChild(link);
  });
  container.appendChild(sourceList);
}

function normalizeQuickQuestions(items) {
  const source = Array.isArray(items) && items.length ? items : DEFAULT_QUICK_QUESTIONS;
  return source.map((item, index) => ({
    id: Number(item.id || Date.now() + index),
    label: String(item.label || "").trim() || "추천 질문",
    message: String(item.message || "").trim() || String(item.label || "").trim(),
    intent: String(item.intent || "").trim(),
    enabled: item.enabled !== false,
    sortOrder: Number(item.sortOrder || index + 1),
  })).sort((a, b) => a.sortOrder - b.sortOrder || a.id - b.id);
}

function loadQuickQuestions() {
  try {
    const stored = JSON.parse(localStorage.getItem(QUICK_QUESTIONS_KEY) || "[]");
    return normalizeQuickQuestions(stored.length ? stored : QUICK_QUESTIONS_BY_LANGUAGE[currentLanguage || "ko"]);
  } catch {
    return normalizeQuickQuestions(QUICK_QUESTIONS_BY_LANGUAGE[currentLanguage || "ko"]);
  }
}

function saveQuickQuestions(items) {
  const normalized = normalizeQuickQuestions(items).map((item, index) => ({ ...item, sortOrder: index + 1 }));
  localStorage.setItem(QUICK_QUESTIONS_KEY, JSON.stringify(normalized));
  renderQuickQuestions();
  renderQuickQuestionManager();
  return normalized;
}

function resetQuickQuestions() {
  localStorage.setItem(QUICK_QUESTIONS_KEY, JSON.stringify(DEFAULT_QUICK_QUESTIONS));
  renderQuickQuestions();
  renderQuickQuestionManager();
}

function renderQuickQuestions() {
  const container = $(".quick-actions");
  if (!container) return;
  const items = loadQuickQuestions().filter((item) => item.enabled).sort((a, b) => a.sortOrder - b.sortOrder);
  container.innerHTML = items.map((item) => (
    `<button class="quick-action-btn" type="button" data-question="${escapeHtml(item.message)}" data-intent="${escapeHtml(item.intent)}">${escapeHtml(item.label)}</button>`
  )).join("");
}

function updateQuickQuestion(id, patch) {
  const items = loadQuickQuestions().map((item) => (item.id === id ? { ...item, ...patch } : item));
  saveQuickQuestions(items);
}

function moveQuickQuestion(id, direction) {
  const items = loadQuickQuestions();
  const index = items.findIndex((item) => item.id === id);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= items.length) return;
  [items[index], items[target]] = [items[target], items[index]];
  saveQuickQuestions(items);
}

function renderQuickQuestionManager() {
  const tbody = $("#quickQuestionRows");
  if (!tbody) return;
  const items = loadQuickQuestions();
  $("#quickQuestionCount").textContent = `총 ${items.length}개`;
  tbody.innerHTML = items.map((item, index) => `
    <tr data-id="${item.id}">
      <td><label class="toggle-cell"><input type="checkbox" data-field="enabled" ${item.enabled ? "checked" : ""}> ON</label></td>
      <td class="row-actions">
        <button type="button" data-action="up" ${index === 0 ? "disabled" : ""}>↑</button>
        <button type="button" data-action="down" ${index === items.length - 1 ? "disabled" : ""}>↓</button>
      </td>
      <td><input data-field="label" value="${escapeHtml(item.label)}"></td>
      <td><input data-field="message" value="${escapeHtml(item.message)}"></td>
      <td><input data-field="intent" value="${escapeHtml(item.intent)}"></td>
      <td class="row-actions">
        <button type="button" data-action="save">저장</button>
        <button type="button" data-action="delete">삭제</button>
      </td>
    </tr>
  `).join("") || '<tr><td colspan="6">등록된 추천 질문이 없습니다.</td></tr>';
}

function loadIconConfig() {
  try {
    return { ...DEFAULT_ICONS, ...JSON.parse(localStorage.getItem(ICON_CONFIG_KEY) || "{}") };
  } catch {
    return { ...DEFAULT_ICONS };
  }
}

function saveIconConfig(config) {
  localStorage.setItem(ICON_CONFIG_KEY, JSON.stringify({ ...loadIconConfig(), ...config }));
  applyIconConfig();
  renderIconManager();
}

function resetIconConfig() {
  localStorage.removeItem(ICON_CONFIG_KEY);
  applyIconConfig();
  renderIconManager();
}

function updateFavicon(url) {
  const selectors = [
    'link[rel="icon"]',
    'link[rel="apple-touch-icon"]',
  ];
  selectors.forEach((selector) => {
    $$(selector).forEach((link) => { link.href = url; });
  });
}

function applyIconConfig() {
  const config = loadIconConfig();
  $$(".launcher-compass img").forEach((img) => { img.src = config.externalIcon; });
  $$(".topbar-logo, .bot-mark img, .loading-icon img").forEach((img) => { img.src = config.internalIcon; });
  updateFavicon(config.faviconIcon);
}

function validatePngFile(file) {
  if (!file) return "파일을 선택해 주세요.";
  if (file.type !== "image/png") return "PNG 파일만 업로드할 수 있습니다.";
  if (file.size > 1024 * 1024) return "아이콘 파일은 1MB 이하를 권장합니다.";
  return "";
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error("파일을 읽지 못했습니다."));
    reader.readAsDataURL(file);
  });
}

function renderIconManager() {
  const config = loadIconConfig();
  const mapping = {
    previewExternalIcon: config.externalIcon,
    previewInternalIcon: config.internalIcon,
    previewFaviconIcon: config.faviconIcon,
  };
  Object.entries(mapping).forEach(([id, src]) => {
    const img = $(`#${id}`);
    if (img) img.src = src;
  });
}

function renderIntentOverview() {
  const container = $("#intentOverview");
  if (!container) return;
  const intentNames = [
    "recent_notice",
    "faculty",
    "schedule",
    "curriculum",
    "graduation",
    "transfer",
    "exam",
    "scholarship",
    "faq",
    "contact",
  ];
  container.innerHTML = `
    <div class="metric"><span>등록된 인텐트 수</span><strong>${intentNames.length}</strong></div>
    <div class="metric"><span>운영 파일</span><strong>data/intents.json</strong></div>
    <div class="metric"><span>검색 라우팅</span><strong>Intent 우선</strong></div>
    <div class="metric"><span>현재 인텐트</span><strong>${intentNames.map(escapeHtml).join(", ")}</strong></div>
  `;
}

async function loadSystemDashboard() {
  const container = $("#systemDashboard");
  if (!container) return;
  container.innerHTML = '<div class="metric"><span>상태</span><strong>불러오는 중…</strong></div>';
  const quickCount = loadQuickQuestions().length;
  try {
    const [health, indexStatus, crawlStatus] = await Promise.all([
      jsonFetch("/api/health"),
      jsonFetch("/api/index/status", { headers: adminHeaders() }).catch(() => ({})),
      jsonFetch("/api/crawl/status", { headers: adminHeaders() }).catch(() => ({})),
    ]);
    const tierCounts = indexStatus.tier_counts || {};
    const indexState = indexStatus.state || indexStatus.runtime?.index_state || (Number(indexStatus.documents || 0) > 0 ? "ready" : "stale");
    const indexStateLabel = indexState === "loading" ? "검색 인덱스 준비 중" : indexState === "ready" ? "검색 가능" : indexState === "failed" ? "검색 인덱스 로딩 실패" : "확인 필요";
    const intentCount = 10;
    const lastCrawl = crawlStatus.updated_at || crawlStatus.result?.timestamp || "";
    container.innerHTML = `
      <div class="metric"><span>등록된 인텐트 수</span><strong>${intentCount}</strong></div>
      <div class="metric"><span>등록된 추천 질문 수</span><strong>${quickCount}</strong></div>
      <div class="metric"><span>검색 인덱스</span><strong>${escapeHtml(indexStateLabel)}</strong></div>
      <div class="metric"><span>크롤링 문서 수</span><strong>${Number(indexStatus.documents || health.index?.documents || 0).toLocaleString("ko-KR")}</strong></div>
      <div class="metric"><span>마지막 크롤링 시간</span><strong>${escapeHtml(lastCrawl ? formatKstDateTime(lastCrawl, true) : "기록 없음")}</strong></div>
      <div class="metric"><span>챗봇 버전</span><strong>ComPass v2.0</strong></div>
      <div class="metric"><span>마지막 배포 시간</span><strong>${escapeHtml(formatKstDateTime(document.lastModified, true))}</strong></div>
      <div class="metric"><span>CORE 문서</span><strong>${Number(tierCounts.CORE || 0).toLocaleString("ko-KR")}</strong></div>
      <div class="metric"><span>최근 공지 포함</span><strong>${Number(tierCounts.ACTIVE_NOTICE || 0).toLocaleString("ko-KR")}</strong></div>
      <div class="metric"><span>운영 상태</span><strong>${health.ok ? "정상" : "확인 필요"}</strong></div>
    `;
  } catch (error) {
    container.innerHTML = `<div class="metric"><span>상태</span><strong>${escapeHtml(error.message)}</strong></div>`;
  }
}

function appendField(container, label, value) {
  if (!value) return;
  const row = document.createElement("div");
  row.className = "answer-field";
  const strong = document.createElement("strong");
  strong.textContent = `${label}:`;
  const span = document.createElement("span");
  span.textContent = translateCardText(value);
  row.append(strong, span);
  container.appendChild(row);
}

function appendSubjectList(container, item) {
  const groups = [
    [currentLanguage === "en" ? "(Undergraduate)" : "(대학)", item.subjects_undergraduate || []],
    [currentLanguage === "en" ? "(Graduate)" : "(대학원)", item.subjects_graduate || []],
  ].filter(([, subjects]) => subjects.length);
  if (!groups.length) return;
  const label = document.createElement("strong");
  label.className = "subjects-label";
  label.textContent = cardText("subjects");
  container.appendChild(label);
  const list = document.createElement("ul");
  list.className = "subject-list";
  groups.forEach(([level, subjects]) => {
    const visibleSubjects = subjects.slice(0, 3).map(displayFacultyCourseName).filter(Boolean);
    if (!visibleSubjects.length) return;
    const li = document.createElement("li");
    const strong = document.createElement("strong");
    strong.textContent = level;
    const summary = visibleSubjects.join(", ");
    const suffix = subjects.length > 3 ? (currentLanguage === "en" ? ", etc." : " 등") : "";
    li.append(strong, document.createTextNode(` ${summary}${suffix}`));
    list.appendChild(li);
  });
  container.appendChild(list);
}

function appendSimpleList(container, labelText, values = []) {
  const displayValues = values
    .map((value) => translateCardText(value))
    .filter((value) => currentLanguage !== "en" || !containsKorean(value));
  if (!displayValues.length) return;
  const label = document.createElement("strong");
  label.className = "subjects-label";
  label.textContent = labelText;
  const list = document.createElement("ul");
  list.className = "subject-list";
  displayValues.slice(0, 5).forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    list.appendChild(item);
  });
  container.append(label, list);
}

function appendKeyValueTable(container, rows = {}) {
  const entries = Object.entries(rows || {}).filter(([, value]) => value);
  if (!entries.length) return;
  const wrap = document.createElement("div");
  wrap.className = "answer-table-wrap";
  const table = document.createElement("table");
  table.className = "answer-table";
  const thead = document.createElement("thead");
  const head = document.createElement("tr");
  [cardText("category"), cardText("details")].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    head.appendChild(th);
  });
  thead.appendChild(head);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  entries.forEach(([key, value]) => {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = key;
    const td = document.createElement("td");
    td.textContent = value;
    tr.append(th, td);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  container.appendChild(wrap);
}

function appendCourseMiniTable(container, items = []) {
  const hint = document.createElement("p");
  hint.className = "table-scroll-hint";
  hint.textContent = cardText("swipeHint");
  const wrap = document.createElement("div");
  wrap.className = "answer-table-wrap curriculum-table-wrap";
  const table = document.createElement("table");
  table.className = "answer-table curriculum-table";
  const thead = document.createElement("thead");
  const head = document.createElement("tr");
  [cardText("courseName"), cardText("category"), cardText("description")].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    head.appendChild(th);
  });
  thead.appendChild(head);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  items.slice(0, 3).forEach((item) => {
    const tr = document.createElement("tr");
    [displayCourseName(item), translateCardText(item.category || ""), displayCourseDescription(item)].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  container.appendChild(hint);
  container.appendChild(wrap);
}

function appendExpandButton(container, cards, totalCount, answerType, messageRow, payload = {}) {
  const limit = Number(payload.display_limit || 3);
  if (cards.length <= limit) return;
  let expanded = false;
  cards.slice(limit).forEach((card) => card.classList.add("is-collapsed-item"));
  const button = document.createElement("button");
  button.type = "button";
  button.className = "answer-expand";
  const action = (payload.actions || []).find((item) => item.type === "expand");
  const expandedLabel = action?.label
    || (answerType === "faculty"
      ? (currentLanguage === "en" ? `View All Faculty (${totalCount})` : `전체 교수진 보기 (${totalCount}명)`)
      : (currentLanguage === "en" ? `View All (${totalCount})` : `전체 보기 (${totalCount}개)`));
  button.textContent = expandedLabel;
  button.setAttribute("aria-expanded", "false");
  button.addEventListener("click", () => {
    expanded = !expanded;
    cards.slice(limit).forEach((card) => card.classList.toggle("is-collapsed-item", !expanded));
    button.textContent = expanded ? cardText("compactView") : expandedLabel;
    button.setAttribute("aria-expanded", String(expanded));
    scrollMessageIntoView(expanded ? cards[limit] : messageRow);
  });
  container.appendChild(button);
}

function appendActionLinks(container, payload) {
  const itemUrls = new Set((payload.items || []).flatMap((item) => [
    item.homepage_url,
    ...(item.actions || []).map((action) => action.url),
  ]).filter(Boolean));
  const seen = new Set();
  const links = (payload.actions || []).filter((action) => {
    if (action.type !== "link" || !action.url) return false;
    if (itemUrls.has(action.url) || /professor\.knou\.ac\.kr/i.test(action.url)) return false;
    const key = `${action.label || ""}|${action.url}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (!links.length) return;
  const actions = document.createElement("div");
  actions.className = "answer-actions";
  links.forEach((action) => {
    const link = document.createElement("a");
    link.className = "answer-link";
    link.href = normalizeUrl(action.url);
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.dataset.actionLabelKo = action.label || buttonLabel("link");
    link.textContent = actionText(link.dataset.actionLabelKo);
    actions.appendChild(link);
  });
  container.appendChild(actions);
}

function normalizeUrl(url) {
  if (!url) return "#";
  if (/^https?:\/\//i.test(url)) return url;
  return url;
}

function appendItemLink(card, item, fallbackUrl = "", fallbackLabel = "자세히 보기") {
  const url = item.source_url || item.fallback_url || fallbackUrl;
  if (!url) return;
  appendDirectLink(card, url, item.link_label || fallbackLabel);
}

function appendDirectLink(card, url, label = "바로가기") {
  if (!url) return;
  const actions = document.createElement("div");
  actions.className = "answer-card-actions";
  const link = document.createElement("a");
  link.className = "answer-link-button";
  link.href = normalizeUrl(url);
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.dataset.actionLabelKo = label || buttonLabel("link");
  link.textContent = actionText(link.dataset.actionLabelKo);
  actions.appendChild(link);
  card.appendChild(actions);
}

function appendItemActions(card, item) {
  const seen = new Set();
  (item.actions || []).forEach((action) => {
    if (action.type !== "link" || !action.url || seen.has(action.url)) return;
    seen.add(action.url);
    appendDirectLink(card, action.url, action.label || "바로가기");
  });
  if (item.homepage_url && !seen.has(item.homepage_url)) {
    appendDirectLink(card, item.homepage_url, I18N.ko.buttons.facultyHomepage);
  }
}

function renderFacultyList(bubble, payload, messageRow) {
  const header = document.createElement("div");
  header.className = "answer-heading";
  const title = document.createElement("strong");
  title.textContent = currentLanguage === "en" ? cardText("facultyTitle") : (payload.answer || cardText("facultyTitle"));
  const count = document.createElement("span");
  count.textContent = currentLanguage === "en"
    ? cardText("facultyDesc")
    : (payload.summary || `총 ${payload.total_count || payload.items.length}명의 교수 정보를 확인했습니다.`);
  header.append(title, count);
  bubble.appendChild(header);

  const list = document.createElement("div");
  list.className = "answer-card-list faculty-list";
  const cards = payload.items.map((item, index) => {
    const card = document.createElement("article");
    card.className = "answer-card faculty-card";
    const heading = document.createElement("h3");
    const badge = document.createElement("span");
    badge.className = "faculty-number";
    badge.textContent = String(index + 1);
    heading.append(badge, document.createTextNode(`${item.name} ${translateCardText(item.position || item.title || "교수")}`));
    card.appendChild(heading);
    appendField(card, cardText("position"), translateCardText(item.position || item.title));
    appendField(card, cardText("email"), item.email);
    appendField(card, cardText("phone"), item.phone);
    appendSubjectList(card, item);
    appendSimpleList(card, cardText("researchArea"), item.research || []);
    appendItemActions(card, item);
    appendItemLink(card, item, payload.source_urls?.[0], I18N.ko.buttons.facultyPage);
    list.appendChild(card);
    return card;
  });
  bubble.appendChild(list);
  appendExpandButton(bubble, cards, payload.total_count || cards.length, "faculty", messageRow, payload);
}

function renderFacultyDetail(bubble, payload, messageRow) {
  renderFacultyList(bubble, { ...payload, display_limit: 1, total_count: payload.items.length }, messageRow);
}

function renderGenericItems(bubble, payload, messageRow) {
  const content = document.createElement("div");
  content.className = "message-content answer-summary";
  content.textContent = payload.answer || "";
  bubble.appendChild(content);
  if (payload.summary) {
    const summary = document.createElement("p");
    summary.className = "answer-lead";
    summary.textContent = payload.summary;
    bubble.appendChild(summary);
  }
  if (payload.note) {
    const note = document.createElement("p");
    note.className = "answer-note";
    note.textContent = payload.note;
    bubble.appendChild(note);
  }
  const list = document.createElement("div");
  list.className = "answer-card-list";
  const cards = payload.items.map((item) => {
    const card = document.createElement("article");
    card.className = "answer-card";
    const heading = document.createElement("h3");
    heading.textContent = payload.answer_type === "course_table"
      ? displayCourseName(item)
      : translateCardText(item.title || item.label || "공식 정보");
    card.appendChild(heading);
    if (item.label && item.value) {
      const value = document.createElement("p");
      value.className = "answer-card-summary";
      value.textContent = item.value;
      card.appendChild(value);
    } else if (payload.answer_type === "course_table") {
      appendField(card, cardText("gradeSemester"), [item.grade, item.semester].filter(Boolean).join(" "));
      appendField(card, cardText("category"), item.category);
      appendField(card, cardText("description"), displayCourseDescription(item));
    } else if (payload.answer_type === "course_recommendation") {
      appendField(card, "추천유형", item.group_name);
      appendField(card, "추천 이유", item.reason);
      appendField(card, "난이도", item.difficulty_hint);
      appendField(card, "학습 부담", item.workload_hint);
      appendField(card, "학점", item.credit ? `${item.credit}학점` : "");
    } else if (payload.answer_type === "course_detail") {
      appendField(card, "과목 개요", item.overview);
      appendField(card, "쉽게 말하면", item.easy_explanation);
      appendSimpleList(card, "주요 학습 내용", item.topics || []);
      appendSimpleList(card, "추천 대상", item.recommended_for || []);
    } else if (payload.answer_type === "course_difficulty") {
      appendField(card, "공식 과목 정보", item.official_overview);
      card.appendChild(document.createElement("br"));
      appendField(card, "참고용 학습 부담", item.difficulty_advice);
      const note = document.createElement("p");
      note.className = "answer-note";
      note.textContent = item.disclaimer;
      card.appendChild(note);
    } else if (payload.answer_type === "notice_list") {
      appendField(card, cardText("date"), formatDateOnly(item.date));
      appendField(card, cardText("summary"), item.description);
    } else if (payload.answer_type === "schedule_list") {
      appendField(card, cardText("period"), formatSchedulePeriod(item));
      appendField(card, cardText("details"), item.description);
    } else {
      appendField(card, cardText("category"), item.category);
      appendField(card, cardText("date"), item.published_at);
    }
    if (item.summary && payload.answer_type !== "course_table") {
      const summary = document.createElement("p");
      summary.className = "answer-card-summary";
      summary.textContent = item.summary.length > 500 ? `${item.summary.slice(0, 500)}…` : item.summary;
      card.appendChild(summary);
    }
    appendItemLink(card, item, payload.source_urls?.[0], "자세히 보기");
    list.appendChild(card);
    return card;
  });
  bubble.appendChild(list);
  appendExpandButton(bubble, cards, payload.total_count || cards.length, payload.answer_type, messageRow, payload);
}

function renderNoticeList(bubble, payload, messageRow) {
  renderGenericItems(bubble, payload, messageRow);
}

function renderCourseTable(bubble, payload, messageRow) {
  renderGenericItems(bubble, payload, messageRow);
}

function renderCurriculumByGrade(bubble, payload) {
  const header = document.createElement("div");
  header.className = "answer-heading";
  const title = document.createElement("strong");
  title.textContent = currentLanguage === "en" ? cardText("curriculumTitle") : (payload.answer || cardText("curriculumTitle"));
  const summary = document.createElement("span");
  summary.textContent = currentLanguage === "en" ? cardText("curriculumDesc") : (payload.summary || cardText("curriculumDesc"));
  header.append(title, summary);
  bubble.appendChild(header);

  const list = document.createElement("div");
  list.className = "answer-card-list curriculum-grade-list";
  (payload.groups || []).forEach((group) => {
    const card = document.createElement("article");
    card.className = "answer-card curriculum-grade-card";
    const heading = document.createElement("h3");
    heading.textContent = translateCardText(group.grade || "학년");
    card.appendChild(heading);
    appendCourseMiniTable(card, group.items || []);
    list.appendChild(card);
  });
  bubble.appendChild(list);
}

function renderScheduleList(bubble, payload, messageRow) {
  renderGenericItems(bubble, payload, messageRow);
}

function renderRecommendation(bubble, payload, messageRow) {
  renderGenericItems(bubble, payload, messageRow);
}

function renderCourseDetail(bubble, payload, messageRow) {
  renderGenericItems(bubble, payload, messageRow);
}

function renderCourseDifficulty(bubble, payload, messageRow) {
  const item = (payload.items || [])[0] || payload;
  const header = document.createElement("div");
  header.className = "answer-heading";
  const title = document.createElement("strong");
  title.textContent = payload.answer || item.title || "과목 학습 부담 안내입니다.";
  header.appendChild(title);
  bubble.appendChild(header);
  if (payload.official_overview || item.official_overview) {
    const overview = document.createElement("p");
    overview.className = "answer-card-summary";
    overview.textContent = payload.official_overview || item.official_overview;
    bubble.appendChild(overview);
  }
  const advice = payload.difficulty_advice || item.difficulty_advice || {};
  if (typeof advice === "object") {
    appendKeyValueTable(bubble, advice);
  } else {
    renderTextAnswer(bubble, String(advice));
  }
  const disclaimer = payload.disclaimer || item.disclaimer;
  if (disclaimer) {
    const note = document.createElement("p");
    note.className = "answer-note";
    note.textContent = disclaimer;
    bubble.appendChild(note);
  }
}

function renderStructuredAdvice(bubble, payload, messageRow) {
  renderGenericItems(bubble, payload, messageRow);
  if (payload.disclaimer) {
    const note = document.createElement("p");
    note.className = "answer-note";
    note.textContent = payload.disclaimer;
    bubble.appendChild(note);
  }
}

function renderGenericCards(bubble, payload, messageRow) {
  renderGenericItems(bubble, payload, messageRow);
}

function appendParagraph(container, lines) {
  if (!lines.length) return;
  const paragraph = document.createElement("p");
  paragraph.className = "message-content text-paragraph";
  paragraph.textContent = lines.join("\n");
  container.appendChild(paragraph);
}

function appendMarkdownTable(container, lines) {
  if (lines.length < 2) return false;
  const rows = lines
    .map((line) => line.trim())
    .filter((line) => line.startsWith("|") && line.endsWith("|"))
    .map((line) => line.slice(1, -1).split("|").map((cell) => cell.trim()));
  if (rows.length < 2 || !rows[1].every((cell) => /^:?-{2,}:?$/.test(cell))) return false;

  const wrap = document.createElement("div");
  wrap.className = "answer-table-wrap";
  const table = document.createElement("table");
  table.className = "answer-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  rows[0].forEach((cell) => {
    const th = document.createElement("th");
    th.textContent = cell;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  rows.slice(2).forEach((row) => {
    const tr = document.createElement("tr");
    row.forEach((cell) => {
      const td = document.createElement("td");
      td.textContent = cell;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  container.appendChild(wrap);
  return true;
}

function renderTextAnswer(bubble, text) {
  const rawText = String(text || "")
    .replace(/검색\s*점수\s*[:：]?\s*\d+(?:\.\d+)?점?/g, "")
    .split(/\r?\n/)
    .filter((line) => {
      const trimmed = line.trim();
      if (!trimmed) return true;
      if (/^\{\s*["']?(title|overview|topics|easy_explanation)["']?\s*:/.test(trimmed)) return false;
      if (/^[\[{].*[\]}],?$/.test(trimmed)) return false;
      if (/\b(dict|list|repr)\b/i.test(trimmed)) return false;
      return true;
    })
    .join("\n");
  const lines = rawText.split(/\r?\n/);
  let paragraph = [];
  let bulletList = null;
  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index];
    const line = raw.trim();
    if (!line) {
      appendParagraph(bubble, paragraph);
      paragraph = [];
      bulletList = null;
      continue;
    }
    if (line.startsWith("|")) {
      appendParagraph(bubble, paragraph);
      paragraph = [];
      bulletList = null;
      const tableLines = [];
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        tableLines.push(lines[index]);
        index += 1;
      }
      index -= 1;
      if (!appendMarkdownTable(bubble, tableLines)) {
        paragraph.push(...tableLines);
      }
      continue;
    }
    const headingMatch = line.match(/^\*\*(.+)\*\*$/);
    if (headingMatch) {
      appendParagraph(bubble, paragraph);
      paragraph = [];
      bulletList = null;
      const heading = document.createElement("strong");
      heading.className = "text-answer-title";
      heading.textContent = headingMatch[1];
      bubble.appendChild(heading);
      continue;
    }
    const bulletMatch = line.match(/^[-•]\s+(.+)$/);
    if (bulletMatch) {
      appendParagraph(bubble, paragraph);
      paragraph = [];
      if (!bulletList) {
        bulletList = document.createElement("ul");
        bulletList.className = "text-bullet-list";
        bubble.appendChild(bulletList);
      }
      const li = document.createElement("li");
      li.textContent = bulletMatch[1];
      bulletList.appendChild(li);
      continue;
    }
    bulletList = null;
    paragraph.push(line);
  }
  appendParagraph(bubble, paragraph);
}

function isIncompleteAnswerText(text) {
  const clean = String(text || "").trim();
  if (!clean) return true;
  const lines = clean.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const last = lines.at(-1) || "";
  if (clean.length < 80 && !/[.!?。요다)\]]$/.test(last)) return true;
  if (/[:：]$/.test(last)) return true;
  if (/(및|또는|그리고|하지만|때문에|위해|수 있도록|하는|입니다만)$/.test(last)) return true;
  if (!/[.!?。요다)\]]$/.test(last)) return true;
  return false;
}

function addMessage(role, text, sources = [], confirmation = false, payload = {}) {
  const row = document.createElement("div");
  row.className = `message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const renderers = {
    faculty: renderFacultyList,
    faculty_detail: renderFacultyDetail,
    notice_list: renderNoticeList,
    course_table: renderCourseTable,
    curriculum_by_grade: renderCurriculumByGrade,
    schedule_list: renderScheduleList,
    course_recommendation: renderRecommendation,
    course_detail: renderCourseDetail,
    course_difficulty: renderCourseDifficulty,
    course_grade_strategy: renderStructuredAdvice,
    course_order: renderStructuredAdvice,
    notice_explain: renderStructuredAdvice,
    schedule_explain: renderStructuredAdvice,
    general_explain: renderStructuredAdvice,
  };
  if (role === "bot" && Array.isArray(payload.items) && payload.items.length) {
    (renderers[payload.answer_type] || renderGenericCards)(bubble, payload, row);
  } else if (role === "bot" && isIncompleteAnswerText(text)) {
    renderTextAnswer(bubble, t("incomplete"));
    const retry = document.createElement("button");
    retry.className = "retry-answer-button";
    retry.type = "button";
    retry.textContent = t("retry");
    retry.onclick = () => sendQuestion(payload.client_question || payload.question || "", {
      allowLlm: true,
      llmType: payload.llm_type || "general_explain",
      context: payload.context || {},
    });
    bubble.appendChild(retry);
  } else {
    renderTextAnswer(bubble, text);
  }
  const hasLinkAction = (payload.actions || []).some((action) => action.type === "link" && action.url);
  if (!hasLinkAction) appendSourceLinks(bubble, sources);
  appendActionLinks(bubble, payload);
  const needsConfirmation = confirmation || (payload.actions || []).some((action) => action.type === "confirm_llm");
  if (needsConfirmation) {
    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    const yes = document.createElement("button");
    const confirmAction = (payload.actions || []).find((action) => action.type === "confirm_llm");
    yes.dataset.buttonLabelKo = confirmAction?.label || I18N.ko.buttons.useAiHelper;
    yes.textContent = translateButtonLabel(yes.dataset.buttonLabelKo);
    yes.onclick = () => {
      yes.disabled = true;
      no.disabled = true;
      actions.remove();
      sendQuestion(payload.client_question || payload.question || "", {
        allowLlm: true,
        llmType: payload.llm_type || "general_explain",
        context: payload.context || {},
      });
    };
    const no = document.createElement("button");
    no.dataset.buttonLabelKo = I18N.ko.buttons.endSearch;
    no.textContent = translateButtonLabel(no.dataset.buttonLabelKo);
    no.onclick = () => actions.remove();
    actions.append(yes, no);
    bubble.appendChild(actions);
  }
  row.appendChild(bubble);
  messages.appendChild(row);
  scrollMessageIntoView(row);
  return row;
}

function addI18nSystemMessage(key) {
  const row = addMessage("bot", t(key), [], false, { i18nKey: key });
  row.dataset.i18nKey = key;
  row.querySelectorAll(".message-content.text-paragraph").forEach((node) => {
    node.dataset.i18nKey = key;
  });
  return row;
}

function ensureIntroMessage() {
  let row = messages.querySelector('[data-intro-message="true"]');
  if (!row) {
    row = addMessage("bot", safeText("welcomeMessage"), [], false, { isWelcome: true });
    row.className = "message bot with-avatar welcome-message message-row assistant";
    row.dataset.introMessage = "true";
    row.dataset.i18nKey = "welcomeMessage";

    messages.prepend(row);
  }

  row.className = "message bot with-avatar welcome-message message-row assistant";
  row.dataset.introMessage = "true";
  row.dataset.i18nKey = "welcomeMessage";

  let icon = row.querySelector(".bot-mark");
  if (!icon) {
    icon = document.createElement("div");
    icon.className = "bot-mark";
    row.prepend(icon);
  }

  let iconImage = icon.querySelector("img.message-avatar");
  if (!iconImage) {
    iconImage = document.createElement("img");
    iconImage.className = "message-avatar";
    iconImage.alt = "ComPass";
    iconImage.onerror = () => { iconImage.style.display = "none"; };
    icon.appendChild(iconImage);
  }

  const bubble = row.querySelector(".bubble");
  bubble?.classList.add("message-bubble", "assistant-bubble");

  const paragraph = row.querySelector(".message-content.text-paragraph");
  if (paragraph) {
    paragraph.dataset.i18nKey = "welcomeMessage";
    paragraph.textContent = safeText("welcomeMessage", paragraph.textContent);
  }
  if (iconImage) iconImage.src = loadIconConfig().internalIcon;
  return row;
}

function createSearchLoading() {
  const requestId = arguments[0] || "";
  const row = document.createElement("div");
  row.className = "message bot search-loading";
  row.dataset.requestId = requestId;
  const bubble = document.createElement("div");
  bubble.className = "bubble loading-bubble";
  const icon = document.createElement("span");
  icon.className = "loading-icon";
  const iconImage = document.createElement("img");
  iconImage.src = loadIconConfig().internalIcon;
  iconImage.alt = "";
  iconImage.onerror = () => { iconImage.style.display = "none"; };
  icon.appendChild(iconImage);
  const copy = document.createElement("div");
  copy.className = "loading-copy";
  const title = document.createElement("strong");
  const subtitle = document.createElement("span");
  subtitle.textContent = t("waitingSub");
  copy.append(title, subtitle);
  bubble.append(icon, copy);
  row.appendChild(bubble);
  messages.appendChild(row);

  const phrase = t("waiting");
  let index = 0;
  let dots = 0;
  const typingTimer = window.setInterval(() => {
    index += 1;
    title.textContent = phrase.slice(0, index);
    if (index >= phrase.length) window.clearInterval(typingTimer);
  }, 40);
  const dotTimer = window.setInterval(() => {
    if (index < phrase.length) return;
    dots = (dots + 1) % 4;
    title.textContent = `${phrase}${".".repeat(dots)}`;
  }, 800);
  scrollMessageIntoView(row);

  return {
    remove() {
      window.clearInterval(typingTimer);
      window.clearInterval(dotTimer);
      if (row.dataset.requestId === requestId) row.remove();
    },
  };
}

async function sendQuestion(raw, options = {}) {
  const allowLlm = Boolean(options.allowLlm);
  const llmType = options.llmType || "";
  const context = options.context || undefined;
  const skipUserBubble = Boolean(options.skipUserBubble);
  const question = raw.trim();
  if (!question) return;
  if (isChatPending) return;
  if (!indexReady && !coldStartActive) {
    rememberPendingChatRequest(question, { allowLlm, llmType, context, skipUserBubble: true });
    startServerRecovery();
    return;
  }
  const requestId = newRequestId();
  const duplicateController = pendingByQuestion.get(question);
  if (duplicateController && !allowLlm) {
    duplicateController.abort();
  }
  const controller = new AbortController();
  pendingByQuestion.set(question, controller);
  pendingRequests.set(requestId, { question, controller });
  if (!allowLlm && !skipUserBubble) {
    addMessage("user", question);
  }
  setChatPending(true);
  const waiting = createSearchLoading(requestId);
  try {
    let result;
    let retryCount = 0;
    do {
      result = await jsonFetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        timeoutMs: SERVER_WAKE_TIMEOUT_MS,
        body: JSON.stringify({
          question,
          allow_llm: allowLlm,
          llm_type: llmType,
          session_id: SESSION_ID,
          request_id: requestId,
          language: currentLanguage || "ko",
          context,
        }),
      });
      if (isColdStartCondition(result)) {
        rememberPendingChatRequest(question, { allowLlm, llmType, context, skipUserBubble: true });
        waiting.remove();
        startServerRecovery();
        return;
      }
      if (result.status !== "index_loading" || retryCount >= INDEX_LOADING_MAX_RETRIES) break;
      retryCount += 1;
      await delay(Number(result.retry_after_ms || INDEX_LOADING_DEFAULT_DELAY_MS));
    } while (pendingRequests.has(requestId));
    if (!pendingRequests.has(requestId) || result.request_id !== requestId) {
      waiting.remove();
      return;
    }
    waiting.remove();
    result.client_question = question;
    let answer = result.answer;
    if (result.mode === "DB_LOAD_ERROR") {
      answer = `지식 DB 로딩에 실패했습니다.\n${result.failure_reason || "관리자에게 서버 로그 확인을 요청해 주세요."}`;
    } else if (result.mode === "INDEX_EMPTY") {
      answer = "백엔드 연결은 정상이지만 검색 인덱스가 비어 있습니다. 관리자 메뉴에서 크롤링 또는 인덱스 재생성을 실행해 주세요.";
    }
    addMessage("bot", answer, result.sources || [], result.requires_llm_confirmation, result);
  } catch (error) {
    if (error.name === "AbortError") {
      waiting.remove();
      return;
    }
    waiting.remove();
    if (isColdStartCondition(error)) {
      rememberPendingChatRequest(question, { allowLlm, llmType, context, skipUserBubble: true });
      startServerRecovery();
      return;
    }
    const prefix =
      error.kind === "BACKEND_CONNECTION"
        ? "백엔드 연결 실패"
        : error.kind === "BACKEND_SERVER"
          ? "백엔드 또는 DB 로딩 실패"
          : "요청 처리 실패";
    addMessage("bot", `${prefix}: ${error.message}`);
  } finally {
    pendingRequests.delete(requestId);
    if (pendingByQuestion.get(question) === controller) pendingByQuestion.delete(question);
    setChatPending(false);
    if (!isMobileDevice()) {
      $("#question").focus({ preventScroll: true });
    }
  }
}

$("#chatForm").addEventListener("submit", (event) => {
  event.preventDefault();
  if (isChatPending || !indexReady || startupGateActive || coldStartActive) return;
  const value = $("#question").value;
  $("#question").value = "";
  sendQuestion(value);
});

$("#languageSelect")?.addEventListener("change", (event) => {
  localStorage.removeItem(QUICK_QUESTIONS_KEY);
  setLanguage(event.target.value);
});
$("#question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("#chatForm").requestSubmit();
  }
});
$("#question").addEventListener("focus", () => {
  updateAppHeight();
});
$(".quick-actions")?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-question]");
  if (!button) return;
  if (isChatPending || !indexReady || startupGateActive || coldStartActive) return;
  sendQuestion(button.dataset.question, {
    context: { quick_intent: button.dataset.intent || "" },
  });
});

$$(".tab").forEach((button) => button.addEventListener("click", () => {
  if (button.dataset.action === "admin-login") {
    openAdminLogin("system");
    return;
  }
  const tabName = button.dataset.tab;
  if (ADMIN_TABS.has(tabName)) enterAdminTab(tabName);
  else activateTab(tabName);
}));

$("#adminLoginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = $("#adminLoginPassword").value;
  const submit = $("#adminLoginSubmit");
  submit.disabled = true;
  $("#adminLoginError").textContent = "";
  try {
    await jsonFetch("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    adminPassword = password;
    sessionStorage.setItem("admin_auth", "true");
    const target = pendingAdminTab;
    closeAdminLogin();
    updateAdminUi();
    await enterAdminTab(target);
  } catch (error) {
    $("#adminLoginError").textContent =
      error.status === 401 ? "비밀번호가 올바르지 않습니다." : error.message;
    $("#adminLoginPassword").select();
  } finally {
    submit.disabled = false;
  }
});
$("#adminLoginClose").addEventListener("click", closeAdminLogin);
$("#adminLoginCancel").addEventListener("click", closeAdminLogin);
$("#adminLoginModal").addEventListener("click", (event) => {
  if (event.target.classList.contains("admin-modal-backdrop")) closeAdminLogin();
});
$("#adminLogout").addEventListener("click", () => {
  adminPassword = "";
  sessionStorage.removeItem("admin_auth");
  updateAdminUi();
  activateTab("chat");
});

$("#refreshSystemDashboard")?.addEventListener("click", loadSystemDashboard);

$("#quickQuestionForm")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const label = $("#quickLabel").value.trim();
  const message = $("#quickMessage").value.trim();
  const intent = $("#quickIntent").value.trim();
  if (!label || !message) return;
  const items = loadQuickQuestions();
  items.push({
    id: Date.now(),
    label,
    message,
    intent,
    enabled: true,
    sortOrder: items.length + 1,
  });
  saveQuickQuestions(items);
  event.currentTarget.reset();
});

$("#resetQuickQuestions")?.addEventListener("click", () => {
  if (confirm("추천 질문을 기본값으로 복원할까요?")) resetQuickQuestions();
});

$("#quickQuestionRows")?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const row = button.closest("tr[data-id]");
  const id = Number(row?.dataset.id || 0);
  const action = button.dataset.action;
  if (action === "up") moveQuickQuestion(id, -1);
  if (action === "down") moveQuickQuestion(id, 1);
  if (action === "delete") saveQuickQuestions(loadQuickQuestions().filter((item) => item.id !== id));
  if (action === "save") {
    updateQuickQuestion(id, {
      label: row.querySelector('[data-field="label"]').value.trim(),
      message: row.querySelector('[data-field="message"]').value.trim(),
      intent: row.querySelector('[data-field="intent"]').value.trim(),
      enabled: row.querySelector('[data-field="enabled"]').checked,
    });
  }
});

$("#quickQuestionRows")?.addEventListener("change", (event) => {
  if (event.target.matches('[data-field="enabled"]')) {
    const row = event.target.closest("tr[data-id]");
    updateQuickQuestion(Number(row.dataset.id), { enabled: event.target.checked });
  }
});

async function handleIconUpload(field, input) {
  const file = input.files?.[0];
  const error = validatePngFile(file);
  const status = $("#iconAdminStatus");
  if (error) {
    status.textContent = error;
    input.value = "";
    return;
  }
  try {
    const dataUrl = await readFileAsDataUrl(file);
    saveIconConfig({ [field]: dataUrl });
    status.textContent = "아이콘이 저장되어 즉시 반영되었습니다.";
  } catch (err) {
    status.textContent = err.message || "아이콘 저장에 실패했습니다.";
  } finally {
    input.value = "";
  }
}

$("#externalIconInput")?.addEventListener("change", (event) => handleIconUpload("externalIcon", event.target));
$("#internalIconInput")?.addEventListener("change", (event) => handleIconUpload("internalIcon", event.target));
$("#faviconIconInput")?.addEventListener("change", (event) => handleIconUpload("faviconIcon", event.target));
$("#resetIconConfig")?.addEventListener("click", () => {
  if (confirm("챗봇 아이콘을 기본값으로 복원할까요?")) {
    resetIconConfig();
    $("#iconAdminStatus").textContent = "기본 아이콘으로 복원했습니다.";
  }
});

async function pollCrawl() {
  const status = await jsonFetch("/api/crawl/status", { headers: adminHeaders() });
  $("#crawlStatus").textContent = status.message || "대기 중";
  renderCrawlProgress(status);
  if (status.running) setTimeout(pollCrawl, 2000);
  else {
    $("#runCrawl").disabled = false;
    $("#crawlDepth").disabled = false;
    if (status.result) loadKnowledge();
  }
}

function renderCrawlProgress(status) {
  const wrap = $("#crawlProgressWrap");
  const progress = status.progress || {};
  const shouldShow = Boolean(status.running || status.result || progress.percent || status.error);
  wrap.hidden = !shouldShow;
  if (!shouldShow) return;

  const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
  $("#crawlProgressBar").style.width = `${percent}%`;
  $("#crawlProgressPercent").textContent = `${percent}%`;
  const updatedAt = status.updated_at ? new Date(status.updated_at) : null;
  const stale = Boolean(status.running && updatedAt && Date.now() - updatedAt.getTime() > 120000);
  const saveInfo = [
    status.saved_count !== undefined ? `저장 ${status.saved_count}` : "",
    status.skipped_count !== undefined ? `유지 ${status.skipped_count}` : "",
    status.failed_count !== undefined ? `실패 ${status.failed_count}` : "",
    status.skipped_old_count !== undefined ? `3년 초과 제외 ${status.skipped_old_count}` : "",
    status.skipped_no_date_count !== undefined ? `게시일 없음 제외 ${status.skipped_no_date_count}` : "",
    status.static_pages !== undefined ? `정적 ${status.static_pages}` : "",
    progress.CORE !== undefined ? `CORE ${progress.CORE}` : "",
    progress.ACTIVE_NOTICE !== undefined ? `공지 ${progress.ACTIVE_NOTICE}` : "",
    progress.TEMPORARY !== undefined ? `임시 ${progress.TEMPORARY}` : "",
    progress.IMPORTANT_ARCHIVE !== undefined ? `중요보관 ${progress.IMPORTANT_ARCHIVE}` : "",
    progress.NOISE !== undefined ? `NOISE ${progress.NOISE}` : "",
  ].filter(Boolean).join(" · ");
  $("#crawlProgressDetail").textContent =
    `Depth ${progress.depth ?? 0}/${progress.max_depth ?? $("#crawlDepth").value} · ` +
    `전체 ${status.total_urls ?? progress.total_urls ?? progress.visited ?? 0} · ` +
    `방문 ${progress.visited ?? 0} · 대기 ${progress.queued ?? 0} · 수집 ${progress.documents ?? 0}` +
    (saveInfo ? ` · ${saveInfo}` : "");
  const current = status.error
    ? `오류: ${status.error}`
    : stale
      ? "작업 응답 없음. 서버 로그 확인 필요"
      : status.current_title
        ? `현재 처리: ${status.current_title}`
        : progress.url || "";
  $("#crawlCurrentUrl").textContent = current;
  $("#crawlCurrentUrl").classList.toggle("is-error", Boolean(status.error || stale));
  const track = wrap.querySelector('[role="progressbar"]');
  track.setAttribute("aria-valuenow", String(percent));
}

$("#setupNotion").addEventListener("click", async () => {
  const status = $("#crawlStatus");
  status.textContent = "Notion DB 필수 컬럼을 구성하고 있습니다…";
  try {
    const result = await jsonFetch("/api/notion/setup", {
      method: "POST",
      headers: adminHeaders(),
    });
    status.textContent = result.message;
    await Promise.all([loadKnowledge(), loadIndexStatus()]);
  } catch (error) {
    status.textContent = error.message;
  }
});

$("#runCrawl").addEventListener("click", async () => {
  try {
    const maxDepth = Number($("#crawlDepth").value);
    $("#runCrawl").disabled = true;
    $("#crawlDepth").disabled = true;
    renderCrawlProgress({
      running: true,
      progress: { percent: 1, depth: 0, max_depth: maxDepth, visited: 0, queued: 0, documents: 0 },
    });
    const result = await jsonFetch("/api/crawl", {
      method: "POST",
      headers: { ...adminHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ max_depth: maxDepth }),
    });
    $("#crawlStatus").textContent = result.message;
    setTimeout(pollCrawl, 800);
  } catch (error) {
    $("#crawlStatus").textContent = error.message;
    $("#runCrawl").disabled = false;
    $("#crawlDepth").disabled = false;
  }
});

async function loadKnowledge() {
  const tbody = $("#knowledgeRows");
  tbody.innerHTML = '<tr><td colspan="5">불러오는 중…</td></tr>';
  try {
    const data = await jsonFetch("/api/knowledge/recent?limit=30", { headers: adminHeaders() });
    tbody.innerHTML = data.items.map((item) => `<tr>
      <td><a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a></td>
      <td>${item.source_type === "community" ? "비공식 커뮤니티" : "공식"}</td>
      <td>${escapeHtml(item.category)}</td><td>${escapeHtml(item.status)}</td><td>${escapeHtml(formatKstDateTime(item.collected_at))}</td>
    </tr>`).join("") || '<tr><td colspan="5">데이터가 없습니다.</td></tr>';
  } catch (error) { tbody.innerHTML = `<tr><td colspan="5">${escapeHtml(error.message)}</td></tr>`; }
}
$("#loadKnowledge").addEventListener("click", loadKnowledge);

async function loadIndexStatus() {
  const data = await jsonFetch("/api/index/status", { headers: adminHeaders() });
  const state = data.state || data.runtime?.index_state || (Number(data.documents || 0) > 0 ? "ready" : "stale");
  const statusLabel = state === "loading" ? "검색 인덱스 준비 중" : state === "ready" ? "검색 가능" : state === "failed" ? "검색 인덱스 로딩 실패" : "검색 인덱스 미생성";
  const jobMessage = state === "ready"
    ? `Indexed : ${Number(data.documents || 0).toLocaleString("ko-KR")} · Documents : ${Number(data.runtime?.notion_document_count || data.documents || 0).toLocaleString("ko-KR")}`
    : data.job?.message || statusLabel;
  $("#indexStatus").innerHTML = `
    <div class="metric"><span>상태</span><strong>${escapeHtml(statusLabel)}</strong></div>
    <div class="metric"><span>문서 수</span><strong>${data.documents}</strong></div>
    <div class="metric"><span>제외 문서</span><strong>${data.excluded || 0}</strong></div>
    <div class="metric"><span>교과목 수</span><strong>${data.courses || 0}</strong></div>
    <div class="metric"><span>생성 시각</span><strong>${escapeHtml(data.built_at ? formatKstDateTime(data.built_at, true) : "미생성")}</strong></div>
    <div class="metric"><span>작업 상태</span><strong>${escapeHtml(jobMessage)}</strong></div>`;
  renderTierRows(data);
}

function renderTierRows(data) {
  const tbody = $("#tierRows");
  if (!tbody) return;
  const included = data.tier_counts || {};
  const excluded = data.excluded_by_tier || {};
  const policies = {
    CORE: "항상 포함",
    ACTIVE_NOTICE: "최근 공지 포함",
    TEMPORARY: "활성 기간만 포함",
    IMPORTANT_ARCHIVE: "중요 보관 포함",
    NOISE: "검색 제외",
  };
  tbody.innerHTML = ["CORE", "ACTIVE_NOTICE", "TEMPORARY", "IMPORTANT_ARCHIVE", "NOISE"].map((tier) => `
    <tr>
      <td>${tier}</td>
      <td>${Number(included[tier] || 0).toLocaleString("ko-KR")}</td>
      <td>${Number(excluded[tier] || 0).toLocaleString("ko-KR")}</td>
      <td>${escapeHtml(policies[tier])}</td>
    </tr>
  `).join("");
}
$("#rebuildIndex").addEventListener("click", async () => {
  try {
    await jsonFetch("/api/index/rebuild", { method: "POST", headers: adminHeaders() });
    await loadIndexStatus();
    setTimeout(loadIndexStatus, 2000);
  } catch (error) { alert(error.message); }
});
$("#reclassifyTiers").addEventListener("click", async () => {
  try {
    $("#reclassifyTiers").disabled = true;
    $("#reclassifyTiers").textContent = "재분류 중…";
    const result = await jsonFetch("/api/data-tier/reclassify", { method: "POST", headers: adminHeaders() });
    $("#crawlStatus").textContent = result.message;
    await loadIndexStatus();
  } catch (error) {
    alert(error.message);
  } finally {
    $("#reclassifyTiers").disabled = false;
    $("#reclassifyTiers").textContent = "데이터 계층 재분류";
  }
});
$("#searchForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await jsonFetch("/api/search/test", {
      method: "POST",
      headers: { ...adminHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ query: $("#searchQuery").value, top_k: 5 }),
    });
    $("#searchResults").innerHTML = data.results.map((item) => `<article class="result-card">
      <h3>${escapeHtml(item.title)} <span class="score">${item.score}점</span></h3>
      <p>${escapeHtml(item.summary || item.body || "").slice(0, 350)}</p>
    </article>`).join("") || '<article class="result-card">검색 결과가 없습니다.</article>';
  } catch (error) { $("#searchResults").innerHTML = `<article class="result-card">${escapeHtml(error.message)}</article>`; }
});

async function loadStats() {
  const tbody = $("#statsRows");
  tbody.innerHTML = '<tr><td colspan="5">불러오는 중…</td></tr>';
  try {
    const data = await jsonFetch("/api/stats?limit=50", { headers: adminHeaders() });
    tbody.innerHTML = data.items.map((item) => `<tr>
      <td>${escapeHtml(formatKstDateTime(item["질문일시"]))}</td><td>${escapeHtml(item["사용자질문"])}</td>
      <td>${escapeHtml(item["응답방식"])}</td><td>${escapeHtml(item["검색점수"])}</td>
      <td>${escapeHtml(item["응답시간"])} ms</td></tr>`).join("") || '<tr><td colspan="5">통계가 없습니다.</td></tr>';
  } catch (error) { tbody.innerHTML = `<tr><td colspan="5">${escapeHtml(error.message)}</td></tr>`; }
}
$("#loadStats").addEventListener("click", loadStats);

async function wakeServer() {
  ensureIntroMessage();
}

async function initializeApp() {
  initializeLanguage();
  ensureIntroMessage();
  applyAppConstants();
  updateI18nKeyedText();
  renderQuickQuestions();
  applyIconConfig();
  updateAdminUi();
  updateAppHeight();
  setChatPending(false);
  await waitForServerReady("startup");
}

initializeApp();
