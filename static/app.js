const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const messages = $("#messages");
const appShell = $("#appShell");
const chatLauncher = $("#chatLauncher");
const history = [];
let pendingQuestion = "";

function activateTab(tabName) {
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === tabName));
  $$(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === `panel-${tabName}`));
}

function openChatWindow() {
  appShell.classList.remove("is-hidden", "fullscreen");
  appShell.classList.add("widget-window");
  chatLauncher.classList.add("is-hidden");
  chatLauncher.setAttribute("aria-expanded", "true");
  $("#toggleFullscreen").textContent = "⛶";
  $("#toggleFullscreen").setAttribute("aria-label", "전체 화면으로 보기");
  activateTab("chat");
  requestAnimationFrame(() => $("#question").focus());
}

function minimizeChat() {
  appShell.classList.add("is-hidden");
  chatLauncher.classList.remove("is-hidden");
  chatLauncher.setAttribute("aria-expanded", "false");
  chatLauncher.focus();
}

function toggleFullscreen() {
  const expanding = !appShell.classList.contains("fullscreen");
  appShell.classList.toggle("fullscreen", expanding);
  appShell.classList.toggle("widget-window", !expanding);
  $("#toggleFullscreen").textContent = expanding ? "↙" : "⛶";
  $("#toggleFullscreen").setAttribute("aria-label", expanding ? "창 모드로 보기" : "전체 화면으로 보기");
  $("#toggleFullscreen").setAttribute("title", expanding ? "창 모드" : "전체 화면");
}

chatLauncher.addEventListener("click", openChatWindow);
$("#minimizeChat").addEventListener("click", minimizeChat);
$("#toggleFullscreen").addEventListener("click", toggleFullscreen);

function adminHeaders() {
  return { "X-Admin-Password": $("#adminPassword").value };
}

async function jsonFetch(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `요청 실패 (${response.status})`);
  return data;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function addMessage(role, text, sources = [], confirmation = false) {
  const row = document.createElement("div");
  row.className = `message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  if (sources.length) {
    const sourceList = document.createElement("div");
    sourceList.className = "source-list";
    sources.forEach((source, index) => {
      const link = document.createElement("a");
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = `출처 ${index + 1}. ${source.title || "공식 페이지"} (${source.score ?? "-"}점)`;
      sourceList.appendChild(link);
    });
    bubble.appendChild(sourceList);
  }
  if (confirmation) {
    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    const yes = document.createElement("button");
    yes.textContent = "LLM 보조 검색";
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
  messages.scrollTop = messages.scrollHeight;
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
  const waiting = document.createElement("div");
  waiting.className = "message bot";
  waiting.innerHTML = '<div class="bubble">공식 데이터를 검색하고 있습니다…</div>';
  messages.appendChild(waiting);
  try {
    const result = await jsonFetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history: history.slice(-8), allow_llm: allowLlm }),
    });
    waiting.remove();
    addMessage("bot", result.answer, result.sources || [], result.requires_llm_confirmation);
    history.push({ role: "assistant", content: result.answer });
  } catch (error) {
    waiting.remove();
    addMessage("bot", `답변 처리 중 오류가 발생했습니다: ${error.message}`);
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
$$("[data-question]").forEach((button) => button.addEventListener("click", () => sendQuestion(button.dataset.question)));

$$(".tab").forEach((button) => button.addEventListener("click", () => {
  activateTab(button.dataset.tab);
  if (button.dataset.tab === "index") loadIndexStatus();
}));

async function pollCrawl() {
  const status = await jsonFetch("/api/crawl/status");
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
  tbody.innerHTML = '<tr><td colspan="4">불러오는 중…</td></tr>';
  try {
    const data = await jsonFetch("/api/knowledge/recent?limit=30", { headers: adminHeaders() });
    tbody.innerHTML = data.items.map((item) => `<tr>
      <td><a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a></td>
      <td>${escapeHtml(item.category)}</td><td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.collected_at)}</td>
    </tr>`).join("") || '<tr><td colspan="4">데이터가 없습니다.</td></tr>';
  } catch (error) { tbody.innerHTML = `<tr><td colspan="4">${escapeHtml(error.message)}</td></tr>`; }
}
$("#loadKnowledge").addEventListener("click", loadKnowledge);

async function loadIndexStatus() {
  const data = await jsonFetch("/api/index/status");
  $("#indexStatus").innerHTML = `
    <div class="metric"><span>문서 수</span><strong>${data.documents}</strong></div>
    <div class="metric"><span>생성 시각</span><strong>${escapeHtml(data.built_at || "미생성")}</strong></div>
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

$("#loadStats").addEventListener("click", async () => {
  const tbody = $("#statsRows");
  tbody.innerHTML = '<tr><td colspan="5">불러오는 중…</td></tr>';
  try {
    const data = await jsonFetch("/api/stats?limit=50", { headers: adminHeaders() });
    tbody.innerHTML = data.items.map((item) => `<tr>
      <td>${escapeHtml(item["질문일시"])}</td><td>${escapeHtml(item["사용자질문"])}</td>
      <td>${escapeHtml(item["응답방식"])}</td><td>${escapeHtml(item["검색점수"])}</td>
      <td>${escapeHtml(item["응답시간"])} ms</td></tr>`).join("") || '<tr><td colspan="5">통계가 없습니다.</td></tr>';
  } catch (error) { tbody.innerHTML = `<tr><td colspan="5">${escapeHtml(error.message)}</td></tr>`; }
});

async function wakeServer() {
  addMessage("bot", "무엇을 도와드릴까요? ComPass는 컴퓨터과학과 공식 홈페이지 정보를 기준으로 학생들의 길을 안내합니다.");
}
wakeServer();
