const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const messages = $("#messages");
const appShell = $("#appShell");
const chatLauncher = $("#chatLauncher");
const ADMIN_TABS = new Set(["system", "crawl", "intents", "index", "stats", "quick", "icons"]);
const APP_CONFIG = window.COMPASS_CONFIG;
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
const LANGUAGE_KEY = "compass_language";
let currentLanguage = localStorage.getItem(LANGUAGE_KEY) || "";
let DEFAULT_CHAT_PLACEHOLDER = "궁금한 컴퓨터과학과 정보를 질문해보세요";
let PENDING_CHAT_PLACEHOLDER = "답변을 준비하고 있습니다...";
const QUICK_QUESTIONS_KEY = "COMPASS_QUICK_QUESTIONS";
const ICON_CONFIG_KEY = "COMPASS_ICON_CONFIG";
const INDEX_LOADING_MAX_RETRIES = 3;
const INDEX_LOADING_DEFAULT_DELAY_MS = 1500;
const DEFAULT_QUICK_QUESTIONS = [
  { id: 1, label: "교육과정", message: "컴퓨터과학과 교육과정을 알려줘", intent: "curriculum", enabled: true, sortOrder: 1 },
  { id: 2, label: "교수진", message: "컴퓨터과학과 교수진을 알려줘", intent: "faculty", enabled: true, sortOrder: 2 },
  { id: 3, label: "최근 공지", message: "컴퓨터과학과 최근 공지를 알려줘", intent: "recent_notice", enabled: true, sortOrder: 3 },
  { id: 4, label: "학과 일정", message: "컴퓨터과학과 학과 일정을 알려줘", intent: "schedule", enabled: true, sortOrder: 4 },
];
const QUICK_QUESTIONS_BY_LANGUAGE = {
  ko: DEFAULT_QUICK_QUESTIONS,
  en: [
    { id: 1, label: "Curriculum", message: "Show me curriculum", intent: "curriculum", enabled: true, sortOrder: 1 },
    { id: 2, label: "Faculty", message: "Who are the professors?", intent: "faculty", enabled: true, sortOrder: 2 },
    { id: 3, label: "Latest Notice", message: "Latest notice", intent: "recent_notice", enabled: true, sortOrder: 3 },
    { id: 4, label: "Schedule", message: "Department schedule", intent: "schedule", enabled: true, sortOrder: 4 },
  ],
};
const I18N = {
  ko: {
    placeholder: "궁금한 컴퓨터과학과 정보를 질문해보세요",
    pending: "답변을 준비하고 있습니다...",
    waiting: "공식 데이터를 검색하고 있습니다",
    waitingSub: "잠시만 기다려주세요.",
    send: "전송",
    pendingButton: "대기",
    subtitleLine2: "· 학생들의 길잡이",
    intro_title: "안녕하세요, ComPass입니다.",
    intro_copy: "공식 정보를 학생이 이해하기 쉽게 정리해 안내합니다.",
    introMessage: "안녕하세요, ComPass입니다.\n공식 정보를 학생이 이해하기 쉽게 정리해 안내합니다.",
    tab_chat: "챗봇",
    tab_admin: "관리자 페이지",
    incomplete: "답변이 완전히 생성되지 않았습니다. 다시 시도해 주세요.",
    retry: "답변 다시 생성",
    confirmNo: "검색 종료",
  },
  en: {
    placeholder: "Ask about CS department info",
    pending: "Preparing the answer...",
    waiting: "Searching official data",
    waitingSub: "Please wait a moment.",
    send: "Send",
    pendingButton: "Wait",
    subtitleLine2: "A guide for students",
    intro_title: "How can I help you?",
    intro_copy: "I can guide you through Computer Science department information.",
    introMessage: "Hello, this is ComPass.\nI summarize official information in a way students can easily understand.",
    tab_chat: "Chat",
    tab_admin: "Admin",
    incomplete: "The answer was not completed. Please try again.",
    retry: "Regenerate answer",
    confirmNo: "End search",
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
  appShell.classList.toggle("is-pending", isChatPending);
  const input = $("#question");
  const sendButton = $("#sendButton");
  if (input) {
    input.disabled = isChatPending;
    input.placeholder = isChatPending ? PENDING_CHAT_PLACEHOLDER : DEFAULT_CHAT_PLACEHOLDER;
  }
  if (sendButton) {
    sendButton.disabled = isChatPending;
    sendButton.textContent = isChatPending ? t("pendingButton") : t("send");
  }
  $$("[data-question]").forEach((button) => {
    button.disabled = isChatPending;
  });
  $$(".confirm-actions button").forEach((button) => {
    button.disabled = isChatPending;
  });
}

function t(key) {
  return (I18N[currentLanguage || "ko"] || I18N.ko)[key] || I18N.ko[key] || key;
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function setLanguage(language) {
  currentLanguage = language === "en" ? "en" : "ko";
  localStorage.setItem(LANGUAGE_KEY, currentLanguage);
  DEFAULT_CHAT_PLACEHOLDER = t("placeholder");
  PENDING_CHAT_PLACEHOLDER = t("pending");
  document.documentElement.lang = currentLanguage;
  $("#languageGate")?.setAttribute("hidden", "");
  const select = $("#languageSelect");
  if (select) select.value = currentLanguage;
  $$("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  applyAppConstants();
  updateI18nKeyedText();
  const input = $("#question");
  if (input) input.placeholder = isChatPending ? PENDING_CHAT_PLACEHOLDER : DEFAULT_CHAT_PLACEHOLDER;
  renderQuickQuestions();
}

function initializeLanguage() {
  const stored = localStorage.getItem(LANGUAGE_KEY);
  if (!stored) {
    $("#languageGate")?.removeAttribute("hidden");
    currentLanguage = "ko";
    DEFAULT_CHAT_PLACEHOLDER = t("placeholder");
    PENDING_CHAT_PLACEHOLDER = t("pending");
    const select = $("#languageSelect");
    if (select) select.value = currentLanguage;
    $$("[data-i18n]").forEach((node) => {
      node.textContent = t(node.dataset.i18n);
    });
    applyAppConstants();
    updateI18nKeyedText();
    return;
  }
  setLanguage(stored);
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
  $$("[data-app-name]").forEach((node) => { node.textContent = APP_CONFIG.appName; });
  $$("[data-app-subtitle-line1]").forEach((node) => { node.textContent = APP_CONFIG.appSubtitleLine1; });
  $$("[data-app-subtitle-line2]").forEach((node) => { node.textContent = t("subtitleLine2"); });
}

function updateI18nKeyedText() {
  $$("[data-i18n-key]").forEach((node) => {
    node.textContent = t(node.dataset.i18nKey);
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
  let response;
  try {
    response = await fetch(url, options);
  } catch (cause) {
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
[".chat-intro", ".suggestions", ".composer"].forEach((selector) => {
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
    `<button data-question="${escapeHtml(item.message)}" data-intent="${escapeHtml(item.intent)}">${escapeHtml(item.label)}</button>`
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
  span.textContent = value;
  row.append(strong, span);
  container.appendChild(row);
}

function appendSubjectList(container, item) {
  const groups = [
    ["(대학)", item.subjects_undergraduate || []],
    ["(대학원)", item.subjects_graduate || []],
  ].filter(([, subjects]) => subjects.length);
  if (!groups.length) return;
  const label = document.createElement("strong");
  label.className = "subjects-label";
  label.textContent = "담당과목";
  container.appendChild(label);
  const list = document.createElement("ul");
  list.className = "subject-list";
  groups.forEach(([level, subjects]) => {
    const li = document.createElement("li");
    const strong = document.createElement("strong");
    strong.textContent = level;
    const summary = subjects.slice(0, 3).join(", ");
    const suffix = subjects.length > 3 ? " 등" : "";
    li.append(strong, document.createTextNode(` ${summary}${suffix}`));
    list.appendChild(li);
  });
  container.appendChild(list);
}

function appendSimpleList(container, labelText, values = []) {
  if (!values.length) return;
  const label = document.createElement("strong");
  label.className = "subjects-label";
  label.textContent = labelText;
  const list = document.createElement("ul");
  list.className = "subject-list";
  values.slice(0, 5).forEach((value) => {
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
  ["항목", "안내"].forEach((label) => {
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
  hint.textContent = "← 좌우로 밀어서 전체 내용을 확인할 수 있습니다.";
  const wrap = document.createElement("div");
  wrap.className = "answer-table-wrap curriculum-table-wrap";
  const table = document.createElement("table");
  table.className = "answer-table curriculum-table";
  const thead = document.createElement("thead");
  const head = document.createElement("tr");
  ["과목명", "구분", "특징"].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    head.appendChild(th);
  });
  thead.appendChild(head);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  items.slice(0, 3).forEach((item) => {
    const tr = document.createElement("tr");
    [item.course_name || item.title || "", item.category || "", item.feature_summary || item.feature || ""].forEach((value) => {
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
    || (answerType === "faculty" ? `전체 교수진 보기 (${totalCount}명)` : `전체 보기 (${totalCount}개)`);
  button.textContent = expandedLabel;
  button.setAttribute("aria-expanded", "false");
  button.addEventListener("click", () => {
    expanded = !expanded;
    cards.slice(limit).forEach((card) => card.classList.toggle("is-collapsed-item", !expanded));
    button.textContent = expanded ? "간단히 보기" : expandedLabel;
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
    link.href = action.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = `${action.label || "바로가기"} ↗`;
    actions.appendChild(link);
  });
  container.appendChild(actions);
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
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = `${label} ↗`;
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
    appendDirectLink(card, item.homepage_url, "교수 홈페이지 바로가기");
  }
}

function renderFacultyList(bubble, payload, messageRow) {
  const header = document.createElement("div");
  header.className = "answer-heading";
  const title = document.createElement("strong");
  title.textContent = payload.answer || "컴퓨터과학과 교수진 정보입니다.";
  const count = document.createElement("span");
  count.textContent = payload.summary || `총 ${payload.total_count || payload.items.length}명의 교수 정보를 확인했습니다.`;
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
    heading.append(badge, document.createTextNode(`${item.name} ${item.position || item.title || "교수"}`));
    card.appendChild(heading);
    appendField(card, "직위", item.position || item.title);
    appendField(card, "이메일", item.email);
    appendField(card, "연락처", item.phone);
    appendSubjectList(card, item);
    appendSimpleList(card, "연구 분야", item.research || []);
    appendItemActions(card, item);
    appendItemLink(card, item, payload.source_urls?.[0], "교수진 페이지 바로가기");
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
    heading.textContent = item.title || item.label || "공식 정보";
    card.appendChild(heading);
    if (item.label && item.value) {
      const value = document.createElement("p");
      value.className = "answer-card-summary";
      value.textContent = item.value;
      card.appendChild(value);
    } else if (payload.answer_type === "course_table") {
      appendField(card, "학년/학기", [item.grade, item.semester].filter(Boolean).join(" "));
      appendField(card, "구분", item.category);
      appendField(card, "특징", item.feature);
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
      appendField(card, "게시일", formatDateOnly(item.date));
      appendField(card, "요약", item.description);
    } else if (payload.answer_type === "schedule_list") {
      appendField(card, "기간", formatSchedulePeriod(item));
      appendField(card, "설명", item.description);
    } else {
      appendField(card, "카테고리", item.category);
      appendField(card, "게시일", item.published_at);
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
  title.textContent = payload.answer || "컴퓨터과학과 교육과정 안내입니다.";
  const summary = document.createElement("span");
  summary.textContent = payload.summary || "학년별 대표 과목을 3개씩 먼저 정리했습니다.";
  header.append(title, summary);
  bubble.appendChild(header);

  const list = document.createElement("div");
  list.className = "answer-card-list curriculum-grade-list";
  (payload.groups || []).forEach((group) => {
    const card = document.createElement("article");
    card.className = "answer-card curriculum-grade-card";
    const heading = document.createElement("h3");
    heading.textContent = group.grade || "학년";
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
    yes.textContent = confirmAction?.label || "LLM 보조 답변 사용";
    if (!confirmAction?.label && currentLanguage === "en") yes.textContent = "Use AI Helper";
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
    no.textContent = t("confirmNo");
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
    row = document.createElement("div");
    row.className = "message bot with-avatar welcome-message";
    row.dataset.introMessage = "true";
    row.dataset.i18nKey = "introMessage";

    const icon = document.createElement("div");
    icon.className = "bot-mark";
    const iconImage = document.createElement("img");
    iconImage.src = loadIconConfig().internalIcon;
    iconImage.alt = "";
    iconImage.setAttribute("aria-hidden", "true");
    iconImage.onerror = () => { iconImage.style.display = "none"; };
    icon.appendChild(iconImage);

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const paragraph = document.createElement("p");
    paragraph.className = "message-content text-paragraph";
    paragraph.dataset.i18nKey = "introMessage";
    bubble.appendChild(paragraph);

    row.append(icon, bubble);
    messages.prepend(row);
  }

  const paragraph = row.querySelector(".message-content.text-paragraph");
  if (paragraph) paragraph.textContent = t("introMessage");
  const iconImage = row.querySelector(".bot-mark img");
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
  const question = raw.trim();
  if (!question) return;
  if (isChatPending) return;
  const requestId = newRequestId();
  const duplicateController = pendingByQuestion.get(question);
  if (duplicateController && !allowLlm) {
    duplicateController.abort();
  }
  const controller = new AbortController();
  pendingByQuestion.set(question, controller);
  pendingRequests.set(requestId, { question, controller });
  if (!allowLlm) {
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
  if (isChatPending) return;
  const value = $("#question").value;
  $("#question").value = "";
  sendQuestion(value);
});

$("#languageSelect")?.addEventListener("change", (event) => {
  localStorage.removeItem(QUICK_QUESTIONS_KEY);
  setLanguage(event.target.value);
});
$$("[data-select-language]").forEach((button) => {
  button.addEventListener("click", () => {
    localStorage.removeItem(QUICK_QUESTIONS_KEY);
    setLanguage(button.dataset.selectLanguage);
  });
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
  if (isChatPending) return;
  sendQuestion(button.dataset.question);
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
initializeLanguage();
wakeServer();
applyAppConstants();
updateI18nKeyedText();
renderQuickQuestions();
applyIconConfig();
updateAdminUi();
updateAppHeight();
