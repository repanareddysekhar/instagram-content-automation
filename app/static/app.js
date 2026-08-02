const $ = (selector) => document.querySelector(selector);
const toast = (message) => {
  $("#toast").textContent = message;
  $("#toast").classList.add("show");
  setTimeout(() => $("#toast").classList.remove("show"), 4200);
};
let dashboardTopics = [];

async function api(path, options = {}) {
  const {headers: optionHeaders = {}, _tokenRetry = false, ...requestOptions} = options;
  const token = sessionStorage.getItem("signalStudioAdminToken");
  const response = await fetch(path, {
    ...requestOptions,
    headers: {
      "Content-Type": "application/json",
      ...(token ? {Authorization: `Bearer ${token}`} : {}),
      ...optionHeaders,
    },
  });
  if (response.status === 401 && !_tokenRetry) {
    sessionStorage.removeItem("signalStudioAdminToken");
    const enteredToken = window.prompt("Enter the Signal Studio admin token");
    if (enteredToken) {
      sessionStorage.setItem("signalStudioAdminToken", enteredToken.trim());
      return api(path, {...options, _tokenRetry: true});
    }
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}

function renderStats(data) {
  const counts = data.counts;
  const values = [
    ["TOPICS SCANNED", counts.topics || 0],
    ["DRAFTS CREATED", counts.posts || 0],
    ["AWAITING APPROVAL", counts.pending_approval || 0],
    ["PUBLISHED", counts.published || 0],
  ];
  $("#stats").innerHTML = values.map(([label, value]) =>
    `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`
  ).join("");
}

function renderPosts(posts) {
  if (!posts.length) {
    $("#posts").innerHTML = `<div class="empty">No drafts yet. Generate a topic to create the first carousel.</div>`;
    return;
  }
  $("#posts").innerHTML = posts.map((post, index) => `
    <article class="post">
      <span class="post-number">${String(index + 1).padStart(2, "0")}</span>
      <div>
        <h3>${escapeHtml(post.title)}</h3>
        <div class="post-meta">
          <span>${post.slides.length} SLIDES</span>
          <span>FACTS ${Math.round(post.fact_score * 100)}%</span>
          <span>DUP ${Math.round(post.duplicate_score * 100)}%</span>
        </div>
      </div>
      <span class="status ${post.status}">${post.status.replaceAll("_", " ")}</span>
      <div class="post-actions">
        ${post.status === "pending_approval" ? `
          <button class="icon-button" title="Approve" onclick="decide(${post.id}, 'approve')">✓</button>
          <button class="icon-button" title="Reject" onclick="decide(${post.id}, 'reject')">×</button>
        ` : ""}
        ${post.assets[0] ? `<a href="/generated/${post.assets[0].split("/").pop()}" target="_blank"><button class="icon-button" title="Preview">↗</button></a>` : ""}
      </div>
    </article>
  `).join("");
}

function updateSourceFilter(topics) {
  const filter = $("#sourceFilter");
  const selected = filter.value;
  const sources = [...new Set(topics.map(topic => topic.source_name))].sort();
  filter.innerHTML = `<option value="">All sources</option>${sources.map(source =>
    `<option value="${escapeHtml(source)}">${escapeHtml(source)}</option>`
  ).join("")}`;
  if (sources.includes(selected)) filter.value = selected;
}

function renderTopics() {
  const search = $("#topicSearch").value.trim().toLowerCase();
  const source = $("#sourceFilter").value;
  const topics = dashboardTopics.filter(topic =>
    (!source || topic.source_name === source) &&
    (!search || `${topic.title} ${topic.summary} ${(topic.tags || []).join(" ")}`.toLowerCase().includes(search))
  );
  $("#topics").innerHTML = topics.length ? topics.slice(0, 30).map(topic => `
    <article class="topic">
      <div>
        <h3>${escapeHtml(topic.title)}</h3>
        <p class="topic-summary">${escapeHtml((topic.summary || "No summary supplied.").slice(0, 180))}</p>
      </div>
      <footer>
        <span>${escapeHtml(topic.source_name)} · SCORE ${Math.round(topic.score)}</span>
        <button class="topic-generate" data-topic-url="${encodeURIComponent(topic.url)}">Generate</button>
      </footer>
    </article>
  `).join("") : `<div class="empty">Topic radar is empty. Scan sources or change the filters.</div>`;
  document.querySelectorAll(".topic-generate").forEach(button => {
    button.addEventListener("click", () => runPipeline(decodeURIComponent(button.dataset.topicUrl)));
  });
}

function renderEvents(events) {
  $("#events").innerHTML = events.length ? events.map(event => {
    const payload = JSON.stringify(event.payload || {});
    return `<div class="event">
      <time>${new Date(event.created_at).toLocaleString()}</time>
      <span class="event-type">${escapeHtml(event.event_type)}</span>
      <span class="event-payload">${escapeHtml(payload === "{}" ? "" : payload)}</span>
    </div>`;
  }).join("") : `<div class="empty">No pipeline events yet.</div>`;
}

async function load() {
  try {
    const data = await api("/api/dashboard");
    $("#mode").textContent = `${data.mode.toUpperCase()} MODE`;
    renderStats(data);
    renderPosts(data.posts);
    dashboardTopics = data.topics;
    updateSourceFilter(dashboardTopics);
    renderTopics();
    renderEvents(data.events || []);
  } catch (error) {
    toast(error.message);
  }
}

async function decide(postId, action) {
  try {
    await api(`/api/posts/${postId}/${action}`, {
      method: "POST",
      body: action === "reject" ? JSON.stringify({note: "Rejected in dashboard"}) : undefined,
    });
    toast(action === "approve" ? "Approved and sent to publishing" : "Draft rejected");
    await load();
  } catch (error) { toast(error.message); }
}
window.decide = decide;

async function runPipeline(topicUrl = null) {
  const button = topicUrl ? null : $("#runButton");
  if (button) {
    button.disabled = true;
    button.firstChild.textContent = "Generating ";
  }
  try {
    await api("/api/pipeline/run", {
      method: "POST",
      body: JSON.stringify({topic_url: topicUrl, force_demo: false}),
    });
    toast("Carousel generated and queued for approval");
    await load();
  } catch (error) { toast(error.message); }
  finally {
    if (button) {
      button.disabled = false;
      button.firstChild.textContent = "Generate top topic ";
    }
  }
}

$("#runButton").addEventListener("click", () => runPipeline());

$("#scanButton").addEventListener("click", async (event) => {
  event.currentTarget.disabled = true;
  try {
    const result = await api("/api/topics/discover", {method: "POST"});
    toast(`Scanned ${result.count} topic(s) from trusted sources`);
    await load();
  } catch (error) { toast(error.message); }
  finally { event.currentTarget.disabled = false; }
});

$("#topicSearch").addEventListener("input", renderTopics);
$("#sourceFilter").addEventListener("change", renderTopics);

$("#syncButton").addEventListener("click", async () => {
  try {
    const result = await api("/api/metrics/sync", {method: "POST"});
    toast(`Metrics: ${result.posts_synced} synced · ${result.posts_skipped || 0} skipped · ${result.posts_failed || 0} failed`);
    await load();
  } catch (error) { toast(error.message); }
});

setInterval(() => $("#clock").textContent = new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}), 1000);
load();
