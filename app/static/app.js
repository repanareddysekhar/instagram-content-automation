const $ = (selector) => document.querySelector(selector);
const toast = (message) => {
  $("#toast").textContent = message;
  $("#toast").classList.add("show");
  setTimeout(() => $("#toast").classList.remove("show"), 3200);
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${response.status}`);
  }
  return response.json();
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
    $("#posts").innerHTML = `<div class="empty">No drafts yet. Run the pipeline to create the first carousel.</div>`;
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

function renderTopics(topics) {
  $("#topics").innerHTML = topics.length ? topics.slice(0, 6).map(topic => `
    <article class="topic">
      <h3>${escapeHtml(topic.title)}</h3>
      <footer><span>${escapeHtml(topic.source_name)}</span><span>SCORE ${Math.round(topic.score)}</span></footer>
    </article>
  `).join("") : `<div class="empty">Topic radar will populate after the first scan.</div>`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}

async function load() {
  try {
    const data = await api("/api/dashboard");
    $("#mode").textContent = `${data.mode.toUpperCase()} MODE`;
    renderStats(data);
    renderPosts(data.posts);
    renderTopics(data.topics);
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

$("#runButton").addEventListener("click", async (event) => {
  event.currentTarget.disabled = true;
  event.currentTarget.firstChild.textContent = "Running pipeline ";
  try {
    await api("/api/pipeline/run", {method: "POST", body: JSON.stringify({force_demo: true})});
    toast("Carousel generated and queued for approval");
    await load();
  } catch (error) { toast(error.message); }
  finally {
    event.currentTarget.disabled = false;
    event.currentTarget.firstChild.textContent = "Run pipeline ";
  }
});

$("#syncButton").addEventListener("click", async () => {
  try {
    const result = await api("/api/metrics/sync", {method: "POST"});
    toast(`Metrics synced for ${result.posts_synced} post(s)`);
    await load();
  } catch (error) { toast(error.message); }
});

setInterval(() => $("#clock").textContent = new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}), 1000);
load();

