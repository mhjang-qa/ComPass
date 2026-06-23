const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const messages = $("#messages");
const appShell = $("#appShell");
const chatLauncher = $("#chatLauncher");
const history = [];
const ADMIN_TABS = new Set(["crawl", "index", "stats"]);
const APP_CONFIG = window.COMPASS_CONFIG;
let pendingQuestion = "";
let pendingAdminTab = "";
let adminPassword = "";
const mobilePointer = window.matchMedia("(pointer: coarse)");

// 새로고침 시 인증을 반드시 다시 받는다. 비밀번호는 브라우저 저장소에 보관하지 않는다.
sessionStorage.removeItem("admin_auth");

const { formatKstDateTime } = window.ComPassTime;

function activateTab(tabName) {
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === tabName));
  $$(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === `panel-${tabName}`));
  appShell.classList.toggle("admin-mode", ADMIN_TABS.has(tabName));
}

function applyAppConstants() {
  $$("[data-app-name]").forEach((node) => { node.textContent = APP_CONFIG.appName; });
  $$("[data-app-subtitle-line1]").forEach((node) => { node.textContent = APP_CONFIG.appSubtitleLine1; });
  $$("[data-app-subtitle-line2]").forEach((node) => { node.textContent = APP_CONFIG.appSubtitleLine2; });
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
  const keyboardOpen = isMobileDevice() && viewportHeight < window.innerHeight - 120;
  document.body.classList.toggle("keyboard-open", keyboardOpen);
  if (keyboardOpen) scrollMessageIntoView(messages.lastElementChild || messages, "auto");
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
  $("#adminLogout").hidden = !isAdminAuthenticated();
}

function openAdminLogin(tabName) {
  pendingAdminTab = tabName;
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
  if (tabName === "crawl") await loadKnowledge();
  if (tabName === "index") await loadIndexStatus();
  if (tabName === "stats") await loadStats();
}

async function jsonFetch(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (cause) {
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
  if (lastMessage) scrollMessageIntoView(lastMessage, "auto");
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
  const links = (payload.actions || []).filter((action) => action.type === "link" && action.url);
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
  const actions = document.createElement("div");
  actions.className = "answer-card-actions";
  const link = document.createElement("a");
  link.className = "answer-link-button";
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = `${item.link_label || fallbackLabel} ↗`;
  actions.appendChild(link);
  card.appendChild(actions);
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
    heading.append(badge, document.createTextNode(`${item.name} ${item.title || "교수"}`));
    card.appendChild(heading);
    appendField(card, "이메일", item.email);
    appendField(card, "연락처", item.phone);
    appendSubjectList(card, item);
    appendItemLink(card, item, payload.source_urls?.[0], "교수진 페이지 바로가기");
    list.appendChild(card);
    return card;
  });
  bubble.appendChild(list);
  appendExpandButton(bubble, cards, payload.total_count || cards.length, "faculty", messageRow, payload);
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
    heading.textContent = item.title || "공식 정보";
    card.appendChild(heading);
    if (payload.answer_type === "course_table") {
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
  renderGenericItems(bubble, payload, messageRow);
}

function renderGenericCards(bubble, payload, messageRow) {
  renderGenericItems(bubble, payload, messageRow);
}

function renderTextAnswer(bubble, text) {
  const content = document.createElement("div");
  content.className = "message-content";
  content.textContent = text;
  bubble.appendChild(content);
}

function addMessage(role, text, sources = [], confirmation = false, payload = {}) {
  const row = document.createElement("div");
  row.className = `message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const renderers = {
    faculty: renderFacultyList,
    notice_list: renderNoticeList,
    course_table: renderCourseTable,
    schedule_list: renderScheduleList,
    course_recommendation: renderRecommendation,
    course_detail: renderCourseDetail,
    course_difficulty: renderCourseDifficulty,
  };
  if (role === "bot" && Array.isArray(payload.items) && payload.items.length) {
    (renderers[payload.answer_type] || renderGenericCards)(bubble, payload, row);
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
    yes.onclick = () => {
      actions.remove();
      sendQuestion(pendingQuestion, true);
    };
    const no = document.createElement("button");
    no.textContent = "검색 종료";
    no.onclick = () => actions.remove();
    actions.append(yes, no);
    bubble.appendChild(actions);
  }
  row.appendChild(bubble);
  messages.appendChild(row);
  scrollMessageIntoView(row);
  return row;
}

function createSearchLoading() {
  const row = document.createElement("div");
  row.className = "message bot search-loading";
  const bubble = document.createElement("div");
  bubble.className = "bubble loading-bubble";
  const icon = document.createElement("span");
  icon.className = "loading-icon";
  icon.textContent = "🔎";
  const copy = document.createElement("div");
  copy.className = "loading-copy";
  const title = document.createElement("strong");
  const subtitle = document.createElement("span");
  subtitle.textContent = "잠시만 기다려주세요.";
  copy.append(title, subtitle);
  bubble.append(icon, copy);
  row.appendChild(bubble);
  messages.appendChild(row);

  const phrase = "공식 데이터를 검색하고 있습니다";
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
      row.remove();
    },
  };
}

async function sendQuestion(raw, allowLlm = false) {
  const question = raw.trim();
  if (!question) return;
  if (!allowLlm) {
    addMessage("user", question);
    history.push({ role: "user", content: question });
    pendingQuestion = question;
  }
  $("#sendButton").disabled = true;
  const waiting = createSearchLoading();
  try {
    const result = await jsonFetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history: history.slice(-8), allow_llm: allowLlm }),
    });
    waiting.remove();
    let answer = result.answer;
    if (result.mode === "DB_LOAD_ERROR") {
      answer = `지식 DB 로딩에 실패했습니다.\n${result.failure_reason || "관리자에게 서버 로그 확인을 요청해 주세요."}`;
    } else if (result.mode === "INDEX_EMPTY") {
      answer = "백엔드 연결은 정상이지만 검색 인덱스가 비어 있습니다. 관리자 메뉴에서 크롤링 또는 인덱스 재생성을 실행해 주세요.";
    }
    addMessage("bot", answer, result.sources || [], result.requires_llm_confirmation, result);
    history.push({ role: "assistant", content: result.answer });
  } catch (error) {
    waiting.remove();
    const prefix =
      error.kind === "BACKEND_CONNECTION"
        ? "백엔드 연결 실패"
        : error.kind === "BACKEND_SERVER"
          ? "백엔드 또는 DB 로딩 실패"
          : "요청 처리 실패";
    addMessage("bot", `${prefix}: ${error.message}`);
  } finally {
    $("#sendButton").disabled = false;
    $("#question").focus();
  }
}

$("#chatForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = $("#question").value;
  $("#question").value = "";
  sendQuestion(value);
});
$("#question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("#chatForm").requestSubmit();
  }
});
$("#question").addEventListener("focus", () => {
  updateAppHeight();
  scrollMessageIntoView(messages.lastElementChild || messages, "auto");
});
$$("[data-question]").forEach((button) => button.addEventListener("click", () => sendQuestion(button.dataset.question)));

$$(".tab").forEach((button) => button.addEventListener("click", () => {
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
$("#adminLoginModal").addEventListener("click", (event) => {
  if (event.target.classList.contains("admin-modal-backdrop")) closeAdminLogin();
});
$("#adminLogout").addEventListener("click", () => {
  adminPassword = "";
  sessionStorage.removeItem("admin_auth");
  updateAdminUi();
  activateTab("chat");
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
  const shouldShow = Boolean(status.running || status.result || progress.percent);
  wrap.hidden = !shouldShow;
  if (!shouldShow) return;

  const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
  $("#crawlProgressBar").style.width = `${percent}%`;
  $("#crawlProgressPercent").textContent = `${percent}%`;
  $("#crawlProgressDetail").textContent =
    `Depth ${progress.depth ?? 0}/${progress.max_depth ?? $("#crawlDepth").value} · ` +
    `방문 ${progress.visited ?? 0} · 대기 ${progress.queued ?? 0} · 수집 ${progress.documents ?? 0}`;
  $("#crawlCurrentUrl").textContent = progress.url || "";
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
  $("#indexStatus").innerHTML = `
    <div class="metric"><span>문서 수</span><strong>${data.documents}</strong></div>
    <div class="metric"><span>교과목 수</span><strong>${data.courses || 0}</strong></div>
    <div class="metric"><span>생성 시각</span><strong>${escapeHtml(data.built_at ? formatKstDateTime(data.built_at, true) : "미생성")}</strong></div>
    <div class="metric"><span>작업 상태</span><strong>${escapeHtml(data.job.message)}</strong></div>`;
}
$("#rebuildIndex").addEventListener("click", async () => {
  try {
    await jsonFetch("/api/index/rebuild", { method: "POST", headers: adminHeaders() });
    await loadIndexStatus();
    setTimeout(loadIndexStatus, 2000);
  } catch (error) { alert(error.message); }
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
  addMessage("bot", "안녕하세요, ComPass입니다.\n공식 정보를 학생이 이해하기 쉽게 정리해 안내합니다.");
}
wakeServer();
applyAppConstants();
updateAdminUi();
updateAppHeight();
