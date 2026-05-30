/**
 * TikTok Farm Dashboard
 * Data layer + UI updates (ui-ux-pro-max: monitoring dashboard patterns)
 */

const API = {
  async get(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(await API._err(res, path));
    return res.json();
  },
  async post(path) {
    const res = await fetch(path, { method: "POST" });
    if (!res.ok) throw new Error(await API._err(res, path));
    return res.json();
  },
  async postJson(path, data) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(await API._err(res, path));
    return res.json();
  },
  async postFormData(path, formData) {
    const res = await fetch(path, { method: "POST", body: formData });
    if (!res.ok) throw new Error(await API._err(res, path));
    return res.json();
  },
  async delete(path) {
    const res = await fetch(path, { method: "DELETE" });
    if (!res.ok) throw new Error(await API._err(res, path));
    return res.json();
  },
  async _err(res, path) {
    try {
      const j = await res.json();
      const d = j.detail;
      if (typeof d === "string") return d;
      if (Array.isArray(d)) return d.map((x) => x.msg || x).join(", ");
      return JSON.stringify(d);
    } catch {
      return `${path} → ${res.status}`;
    }
  },
};

let statusChart = null;
let performanceChart = null;
let refreshTimer = null;
let accountsCache = [];
let proxiesCache = [];
let accountPage = 1;
let proxyPage = 1;
const PAGE_SIZE = 25;
let accountModalTab = "account-single";
let proxyModalTab = "proxy-single";

const STATUS_COLORS = {
  active: "#10b981",
  warming: "#3b82f6",
  pending: "#f59e0b",
  banned: "#ef4444",
  shadowbanned: "#f97316",
  paused: "#64748b",
};

const BADGE_CLASS = {
  active: "badge-active",
  warming: "badge-warming",
  pending: "badge-pending",
  banned: "badge-banned",
  shadowbanned: "badge-shadowbanned",
  paused: "badge-paused",
};

function toast(message, type = "success") {
  const container = document.getElementById("toastContainer");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  container.appendChild(el);
  if (type !== "loading") {
    setTimeout(() => el.remove(), 4000);
  }
  return el;
}

function clearToast(el) {
  if (el && el.parentNode) el.remove();
}

function setButtonLoading(btn, loading) {
  if (!btn) return;
  if (loading) {
    btn.disabled = true;
    btn.classList.add("loading");
    btn.dataset.originalText = btn.textContent;
  } else {
    btn.disabled = false;
    btn.classList.remove("loading");
  }
}

function formatNum(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n ?? 0);
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function initials(username) {
  return (username || "?").slice(0, 2).toUpperCase();
}

async function refreshAll() {
  const btn = document.getElementById("btnRefresh");
  if (btn) btn.disabled = true;
  try {
    await Promise.all([
      refreshStats(),
      refreshCharts(),
      refreshAccounts(),
      refreshProxies(),
      refreshAlerts(),
      refreshSettings(),
    ]);
    updateLastSync();
  } catch (e) {
    console.error(e);
    toast("Failed to refresh data", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function updateLastSync() {
  const el = document.getElementById("lastSync");
  if (el) el.textContent = new Date().toLocaleTimeString();
}

async function refreshStats() {
  const [health, perf] = await Promise.all([
    API.get("/api/health"),
    API.get("/api/performance"),
  ]);

  if (!health.success || !perf.success) return;

  const h = health.health;
  const s = perf.stats;
  const statuses = h.account_statuses || {};
  const flagged =
    (statuses.banned || 0) + (statuses.shadowbanned || 0);
  const alerts = s.unresolved_alerts || 0;

  const pill = document.getElementById("systemStatus");
  const statusText = document.getElementById("systemStatusText");
  const statusMsg =
    alerts > 0 ? `${alerts} alerts` : flagged > 0 ? `${flagged} flagged` : "All systems go";
  if (pill) {
    pill.className = "status-pill " + (alerts > 0 ? "warn" : flagged > 0 ? "err" : "ok");
  }
  if (statusText) statusText.textContent = statusMsg;

  updateNavBadges({
    accounts: h.total_accounts || 0,
    proxies: h.total_proxies || 0,
    alerts,
  });

  const sched = document.getElementById("schedulerStatus");
  if (sched) {
    sched.textContent = h.scheduler_running ? "Scheduler: running" : "Scheduler: stopped";
  }

  const bento = document.getElementById("bentoKpis");
  if (!bento) return;

  const cards = [
    {
      label: "Total accounts",
      value: h.total_accounts || 0,
      sub: `${statuses.active || 0} active · ${statuses.warming || 0} warming`,
      cls: "highlight wide",
      icon: "users",
    },
    {
      label: "Posts published",
      value: formatNum(s.posts_posted || 0),
      sub: `${formatNum(s.total_views || 0)} total views`,
      cls: "success",
    },
    {
      label: "Engagement",
      value: (s.avg_engagement_per_post || 0).toFixed(1),
      sub: "avg per post",
      cls: "",
    },
    {
      label: "Proxies online",
      value: h.proxy_statuses?.active || 0,
      sub: `${h.total_proxies || 0} configured`,
      cls: "",
    },
    {
      label: "Flagged",
      value: flagged,
      sub: "banned / shadowbanned",
      cls: flagged > 0 ? "danger" : "",
    },
    {
      label: "Open alerts",
      value: alerts,
      sub: "needs attention",
      cls: alerts > 0 ? "warning" : "",
    },
  ];

  bento.innerHTML = cards
    .map(
      (c) => `
    <article class="kpi ${c.cls}">
      <div class="kpi-label">${c.label}</div>
      <div class="kpi-value">${c.value}</div>
      <div class="kpi-sub">${c.sub}</div>
    </article>`
    )
    .join("");
}

async function refreshCharts() {
  const [health, perf] = await Promise.all([
    API.get("/api/health"),
    API.get("/api/performance"),
  ]);
  if (!health.success || !perf.success) return;

  const statuses = health.health.account_statuses || {};
  const s = perf.stats;
  const labels = Object.keys(statuses);
  const values = Object.values(statuses);

  const statusCtx = document.getElementById("statusChart");
  if (statusCtx) {
    if (statusChart) statusChart.destroy();
    statusChart = new Chart(statusCtx, {
      type: "doughnut",
      data: {
        labels: labels.map((l) => l.charAt(0).toUpperCase() + l.slice(1)),
        datasets: [
          {
            data: values.length ? values : [1],
            backgroundColor: labels.map((l) => STATUS_COLORS[l] || "#64748b"),
            borderWidth: 0,
            hoverOffset: 8,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: {
            position: "bottom",
            labels: { color: "#94a3b8", padding: 14, usePointStyle: true },
          },
        },
      },
    });
  }

  const perfCtx = document.getElementById("performanceChart");
  if (perfCtx) {
    if (performanceChart) performanceChart.destroy();
    performanceChart = new Chart(perfCtx, {
      type: "bar",
      data: {
        labels: ["Views", "Likes", "Comments", "Shares"],
        datasets: [
          {
            label: "Total",
            data: [
              s.total_views || 0,
              s.total_likes || 0,
              s.total_comments || 0,
              s.total_shares || 0,
            ],
            backgroundColor: [
              "rgba(37, 244, 238, 0.75)",
              "rgba(16, 185, 129, 0.75)",
              "rgba(245, 158, 11, 0.75)",
              "rgba(254, 44, 85, 0.75)",
            ],
            borderRadius: 8,
            borderSkipped: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: {
            beginAtZero: true,
            ticks: { color: "#64748b", font: { family: "'JetBrains Mono'" } },
            grid: { color: "rgba(255,255,255,0.06)" },
          },
          x: {
            ticks: { color: "#94a3b8" },
            grid: { display: false },
          },
        },
      },
    });
  }
}

function filterAccounts(list) {
  const q = (document.getElementById("accountSearch")?.value || "").trim().toLowerCase();
  if (!q) return list;
  return list.filter(
    (a) =>
      (a.username || "").toLowerCase().includes(q) ||
      String(a.id).includes(q) ||
      (a.status || "").toLowerCase().includes(q)
  );
}

function filterProxies(list) {
  const q = (document.getElementById("proxySearch")?.value || "").trim().toLowerCase();
  if (!q) return list;
  return list.filter(
    (p) =>
      (p.ip || "").toLowerCase().includes(q) ||
      String(p.port || "").includes(q) ||
      (p.endpoint || `${p.ip}:${p.port}`).toLowerCase().includes(q) ||
      (p.url || "").toLowerCase().includes(q) ||
      String(p.id).includes(q) ||
      (p.status || "").toLowerCase().includes(q)
  );
}

function formatProxyUrl(p) {
  if (p.url) return p.url;
  const auth =
    p.username && p.password ? `${p.username}:${p.password}@` : "";
  return `${p.protocol || "http"}://${auth}${p.ip}:${p.port}`;
}

function copyProxyUrl(p) {
  const url = formatProxyUrl(p);
  navigator.clipboard
    .writeText(url)
    .then(() => toast("Copied proxy URL"))
    .catch(() => toast("Could not copy", "error"));
}

function paginate(list, page) {
  const total = list.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const p = Math.min(Math.max(1, page), pages);
  const start = (p - 1) * PAGE_SIZE;
  return { slice: list.slice(start, start + PAGE_SIZE), page: p, pages, total };
}

function renderPagination(containerId, page, pages, total, onPage) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (total <= PAGE_SIZE) {
    el.innerHTML = `<span>${total} item(s)</span><span></span>`;
    return;
  }
  el.innerHTML = `
    <span>${total} item(s) · page ${page}/${pages}</span>
    <div class="pagination">
      <button class="btn btn-sm" type="button" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Prev</button>
      <button class="btn btn-sm" type="button" data-page="${page + 1}" ${page >= pages ? "disabled" : ""}>Next</button>
    </div>`;
  el.querySelectorAll("[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const np = parseInt(btn.dataset.page, 10);
      if (!Number.isNaN(np)) onPage(np);
    });
  });
}

async function refreshAccounts() {
  const wrap = document.getElementById("accountsTable");
  const data = await API.get("/api/accounts");
  if (!data.success) {
    wrap.innerHTML = `<div class="empty-state">Failed to load accounts</div>`;
    return;
  }

  accountsCache = data.accounts || [];
  renderAccountsTable();
  const badge = document.getElementById("navBadgeAccounts");
  if (badge) badge.textContent = String(accountsCache.length);
}

function renderAccountsTable() {
  const wrap = document.getElementById("accountsTable");
  const filtered = filterAccounts(accountsCache);
  const { slice, page, pages, total } = paginate(filtered, accountPage);
  accountPage = page;

  if (!accountsCache.length) {
    wrap.innerHTML = `
      <div class="empty-state">
        <p>No accounts yet</p>
        <p style="margin-top:0.5rem;font-size:0.8rem">Use <strong>Add</strong> or <strong>Import CSV</strong> for bulk (100+ rows)</p>
        <p style="margin-top:0.35rem;font-size:0.75rem;color:var(--text-dim)">After adding, use <strong>Lookup</strong> or <strong>Sync TikTok</strong> to pull followers &amp; posts (needs TIKTOK_MS_TOKEN)</p>
      </div>`;
    return;
  }

  if (!filtered.length) {
    wrap.innerHTML = `<div class="empty-state">No accounts match your search</div>`;
    return;
  }

  wrap.innerHTML = `
    <div class="table-meta" id="accountsMeta"></div>
    <table>
      <thead>
        <tr>
          <th>Account</th>
          <th>Status</th>
          <th>Followers</th>
          <th>Posts</th>
          <th>Views</th>
          <th>Last active</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        ${slice
          .map(
            (a) => `
          <tr>
            <td>
              <div class="user-cell">
                <div class="avatar">${initials(a.username)}</div>
                <div>
                  <strong>@${escapeHtml(a.username)}</strong>
                  <div style="font-size:0.7rem;color:var(--text-dim)">ID ${a.id} · proxy ${a.proxy_id || "—"}</div>
                </div>
              </div>
            </td>
            <td><span class="badge ${BADGE_CLASS[a.status] || "badge-pending"}">${a.status}</span></td>
            <td style="font-family:var(--font-mono)">${formatNum(a.followers)}</td>
            <td style="font-family:var(--font-mono)">${a.total_posts || 0}</td>
            <td style="font-family:var(--font-mono)">${formatNum(a.total_views)}</td>
            <td style="font-size:0.8rem;color:var(--text-muted)">${formatDate(a.last_active)}</td>
            <td>
              <div class="actions">
                <button class="btn btn-sm" type="button" data-action="farm" data-id="${a.id}">Farm</button>
                <button class="btn btn-sm" type="button" data-action="post" data-id="${a.id}">Post</button>
                <button class="btn btn-sm" type="button" data-action="sync" data-id="${a.id}" title="Sync TikTok profile">Sync</button>
                <button class="btn btn-sm" type="button" data-action="check" data-id="${a.id}">Check</button>
                <button class="btn btn-sm" type="button" data-action="delete" data-id="${a.id}" title="Delete">Del</button>
              </div>
            </td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`;

  renderPagination("accountsMeta", page, pages, total, (p) => {
    accountPage = p;
    renderAccountsTable();
  });

  wrap.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = parseInt(btn.dataset.id, 10);
      if (btn.dataset.action === "delete") deleteAccount(id);
      else runAction(btn.dataset.action, id);
    });
  });
  if (typeof lucide !== "undefined") lucide.createIcons();
}

async function refreshProxies() {
  const wrap = document.getElementById("proxiesTable");
  if (!wrap) return;
  const data = await API.get("/api/proxies");
  if (!data.success) {
    wrap.innerHTML = `<div class="empty-state">Failed to load proxies</div>`;
    return;
  }
  proxiesCache = data.proxies || [];
  renderProxiesTable();
  const badge = document.getElementById("navBadgeProxies");
  if (badge) badge.textContent = String(proxiesCache.length);
}

function renderProxiesTable() {
  const wrap = document.getElementById("proxiesTable");
  const filtered = filterProxies(proxiesCache);
  const { slice, page, pages, total } = paginate(filtered, proxyPage);
  proxyPage = page;

  if (!proxiesCache.length) {
    wrap.innerHTML = `<div class="empty-state"><p>No proxies yet</p><p style="margin-top:0.5rem;font-size:0.8rem">Import CSV or add one manually</p></div>`;
    return;
  }

  if (!filtered.length) {
    wrap.innerHTML = `<div class="empty-state">No proxies match your search</div>`;
    return;
  }

  wrap.innerHTML = `
    <div class="table-meta" id="proxiesMeta"></div>
    <table class="proxy-table">
      <thead>
        <tr>
          <th>ID</th>
          <th>IP</th>
          <th>Port</th>
          <th>Proxy URL</th>
          <th>Auth</th>
          <th>Status</th>
          <th>Fails</th>
          <th>Checked</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${slice
          .map((p) => {
            const endpoint = p.endpoint || `${p.ip}:${p.port}`;
            const proxyUrl = formatProxyUrl(p);
            const hasAuth = Boolean(p.username);
            const checked = formatDate(p.last_checked);
            return `
          <tr>
            <td class="cell-mono">${p.id}</td>
            <td class="cell-mono proxy-ip">${escapeHtml(p.ip)}</td>
            <td class="cell-mono">${p.port}</td>
            <td class="proxy-url-cell">
              <code class="proxy-url" title="${escapeHtml(proxyUrl)}">${escapeHtml(proxyUrl)}</code>
              <button type="button" class="btn btn-sm btn-copy-proxy" data-copy-proxy="${p.id}" title="Copy proxy URL">
                <i data-lucide="copy"></i>
              </button>
            </td>
            <td>${hasAuth ? '<span class="badge badge-warming">yes</span>' : '<span class="text-dim">—</span>'}</td>
            <td><span class="badge ${p.status === "active" ? "badge-active" : "badge-banned"}">${escapeHtml(p.status)}</span></td>
            <td class="cell-mono">${p.fail_count || 0}</td>
            <td class="cell-dim">${escapeHtml(checked)}</td>
            <td><button class="btn btn-sm" type="button" data-delete-proxy="${p.id}">Del</button></td>
          </tr>`;
          })
          .join("")}
      </tbody>
    </table>`;

  renderPagination("proxiesMeta", page, pages, total, (p) => {
    proxyPage = p;
    renderProxiesTable();
  });

  wrap.querySelectorAll("[data-delete-proxy]").forEach((btn) => {
    btn.addEventListener("click", () => deleteProxy(parseInt(btn.dataset.deleteProxy, 10)));
  });
  wrap.querySelectorAll("[data-copy-proxy]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = parseInt(btn.dataset.copyProxy, 10);
      const p = proxiesCache.find((x) => x.id === id);
      if (p) copyProxyUrl(p);
    });
  });
  if (typeof lucide !== "undefined") lucide.createIcons();
}

async function deleteAccount(id) {
  if (!confirm(`Delete account #${id}?`)) return;
  try {
    await API.delete(`/api/accounts/${id}`);
    toast("Account deleted");
    refreshAll();
  } catch (e) {
    toast(e.message, "error");
  }
}

async function deleteProxy(id) {
  if (!confirm(`Delete proxy #${id}? Accounts using it need reassignment.`)) return;
  try {
    await API.delete(`/api/proxies/${id}`);
    toast("Proxy deleted");
    refreshAll();
  } catch (e) {
    toast(e.message, "error");
  }
}

async function refreshAlerts() {
  const wrap = document.getElementById("alertsList");
  const data = await API.get("/api/alerts?resolved=0");
  if (!data.success) {
    wrap.innerHTML = `<div class="empty-state">Failed to load alerts</div>`;
    return;
  }

  const alerts = data.alerts || [];
  const alertBadge = document.getElementById("navBadgeAlerts");
  if (alertBadge) alertBadge.textContent = String(alerts.length);

  if (!alerts.length) {
    wrap.innerHTML = `
      <div class="empty-state">
        <p>All clear</p>
        <p style="font-size:0.8rem;margin-top:0.35rem">No unresolved alerts</p>
      </div>`;
    return;
  }

  wrap.innerHTML = `<div class="alerts-grid">${alerts
    .map(
      (a) => `
      <div class="alert-card type-${escapeHtml(a.alert_type || "info")}">
        <strong>${escapeHtml(a.alert_type || "alert")}</strong>
        <div style="margin-top:0.35rem;color:var(--text-muted);font-size:0.85rem">${escapeHtml(a.message || "")}</div>
        <div class="alert-meta">${formatDate(a.created_at)}${a.username ? " · @" + escapeHtml(a.username) : ""}</div>
        <button class="btn btn-sm" style="margin-top:0.5rem" type="button" data-resolve="${a.id}">Resolve</button>
      </div>`
    )
    .join("")}</div>`;

  wrap.querySelectorAll("[data-resolve]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await API.post(`/api/alerts/${btn.dataset.resolve}/resolve`);
        toast("Alert resolved");
        refreshAlerts();
        refreshStats();
      } catch (e) {
        toast(e.message, "error");
      }
    });
  });
}

function renderProfilePreview(el, profile) {
  if (!el) return;
  el.hidden = false;
  el.innerHTML = `
    <strong>@${escapeHtml(profile.username)}</strong>
    ${profile.verified ? ' <span class="badge badge-active">verified</span>' : ""}
    <div>${escapeHtml(profile.nickname || "")}</div>
    ${profile.bio ? `<div style="color:var(--text-dim);margin-top:0.25rem">${escapeHtml(profile.bio)}</div>` : ""}
    <div class="profile-stats">
      <span>${formatNum(profile.followers)} followers</span>
      <span>${formatNum(profile.following)} following</span>
      <span>${profile.video_count ?? 0} videos</span>
      <span>${formatNum(profile.heart_count)} likes</span>
    </div>
    <a href="${escapeHtml(profile.profile_url)}" target="_blank" rel="noopener" style="font-size:0.75rem;margin-top:0.35rem;display:inline-block">Open on TikTok</a>
  `;
}

async function lookupTikTokProfile() {
  const username = document.getElementById("accUsername")?.value?.trim();
  const preview = document.getElementById("accountProfilePreview");
  if (!username) {
    toast("Enter a TikTok username first", "error");
    return;
  }
  const btn = document.getElementById("btnLookupProfile");
  setButtonLoading(btn, true);
  const toastEl = toast("Fetching TikTok profile\u2026", "loading");
  try {
    const r = await API.get(`/api/tiktok/profile/${encodeURIComponent(username)}`);
    clearToast(toastEl);
    renderProfilePreview(preview, r.profile);
    toast(`@${r.profile.username} \u2014 ${formatNum(r.profile.followers)} followers`);
  } catch (e) {
    clearToast(toastEl);
    if (preview) {
      preview.hidden = false;
      preview.innerHTML = `<span style="color:var(--danger)">${escapeHtml(e.message)}</span>`;
    }
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function syncAccountProfile(accountId) {
  const btn = document.querySelector(`[data-action="sync"][data-id="${accountId}"]`);
  setButtonLoading(btn, true);
  const toastEl = toast("Syncing TikTok profile\u2026", "loading");
  try {
    const r = await API.post(`/api/actions/sync-profile/${accountId}`);
    clearToast(toastEl);
    toast(`@${r.profile?.username || accountId}: ${formatNum(r.profile?.followers)} followers`);
    refreshAccounts();
    refreshStats();
  } catch (e) {
    clearToast(toastEl);
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function syncAllAccountProfiles() {
  const btn = document.getElementById("btnSyncAllProfiles");
  setButtonLoading(btn, true);
  const toastEl = toast("Syncing all accounts from TikTok\u2026", "loading");
  try {
    const r = await API.post("/api/accounts/sync-profiles");
    clearToast(toastEl);
    const res = r.results || {};
    toast(`Synced ${res.synced || 0}, failed ${res.failed || 0}`);
    refreshAccounts();
    refreshStats();
  } catch (e) {
    clearToast(toastEl);
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function runAction(action, accountId) {
  if (action === "sync") {
    return syncAccountProfile(accountId);
  }
  const btn = document.querySelector(`[data-action="${action}"][data-id="${accountId}"]`);
  setButtonLoading(btn, true);
  const paths = {
    farm: `/api/actions/farm/${accountId}`,
    post: `/api/actions/post/${accountId}`,
    check: `/api/actions/check/${accountId}`,
  };
  const toastEl = toast(`Starting ${action} for account ${accountId}\u2026`, "loading");
  try {
    const result = await API.post(paths[action]);
    clearToast(toastEl);
    toast(result.message || `${action} completed`);
    setTimeout(refreshAll, 2000);
  } catch (e) {
    clearToast(toastEl);
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function checkAllProxies() {
  const btn = document.getElementById("btnProxyCheck");
  setButtonLoading(btn, true);
  const toastEl = toast("Checking proxies\u2026", "loading");
  try {
    const r = await API.post("/api/proxies/check");
    clearToast(toastEl);
    const res = r.results || {};
    toast(`Proxies: ${res.alive ?? 0} alive, ${res.dead ?? 0} dead`);
    refreshProxies();
    refreshStats();
  } catch (e) {
    clearToast(toastEl);
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function rescheduleJobs() {
  const btn = document.getElementById("btnReschedule");
  setButtonLoading(btn, true);
  const toastEl = toast("Rescheduling jobs\u2026", "loading");
  try {
    const r = await API.post("/api/scheduler/reschedule");
    clearToast(toastEl);
    toast(r.message || "Jobs rescheduled");
  } catch (e) {
    clearToast(toastEl);
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str ?? "";
  return d.innerHTML;
}

// ---- Settings ----

async function refreshSettings() {
  const wrap = document.getElementById("settingsPanel");
  if (!wrap) return;
  try {
    const r = await API.get("/api/settings/tiktok-api");
    if (!r.success) {
      wrap.innerHTML = `<div class="empty-state">Failed to load settings</div>`;
      return;
    }
    const s = r.settings;
    wrap.innerHTML = `
      <div class="settings-card">
        <h4><i data-lucide="radio" style="width:16px;height:16px"></i> TikTok API</h4>
        <div class="status-line">
          Status: ${s.ready ? '<span class="status-dot-sm ok"></span>Ready' : s.installed ? '<span class="status-dot-sm warn"></span>Not configured' : '<span class="status-dot-sm err"></span>TikTokApi not installed'}
        </div>
        <div class="status-line">
          Library: ${s.installed ? '<code>installed</code>' : '<code>not installed</code>'}
          ${s.ms_token ? '&middot; <code>ms_token set</code>' : '&middot; <code>no ms_token</code>'}
          ${s.browser ? '&middot; Browser: ' + s.browser : ''}
        </div>
        <div class="field-row">
          <input id="settingsMsToken" type="password" placeholder="Paste ms_token here" value="${s.ms_token ? '********' : ''}">
          <button class="btn btn-sm" type="button" id="btnSaveToken">Save</button>
          <button class="btn btn-sm" type="button" id="btnTestApi">Test</button>
        </div>
        <div id="settingsResult" style="margin-top:0.5rem"></div>
      </div>
      <div class="settings-card">
        <h4><i data-lucide="info" style="width:16px;height:16px"></i> How to get ms_token</h4>
        <div style="font-size:0.8rem;color:var(--text-muted);line-height:1.6">
          1. Log into TikTok on Chrome/Firefox<br>
          2. Open DevTools (F12) &rarr; Application tab &rarr; Cookies &rarr; tiktok.com<br>
          3. Find <code>ms_token</code>, copy its value<br>
          4. Paste above and click Save, then Test
        </div>
      </div>`;

    if (typeof lucide !== "undefined") lucide.createIcons();

    document.getElementById("btnSaveToken")?.addEventListener("click", saveMsToken);
    document.getElementById("btnTestApi")?.addEventListener("click", testTikTokApi);
  } catch (e) {
    wrap.innerHTML = `<div class="empty-state">Error: ${escapeHtml(e.message)}</div>`;
  }
}

async function saveMsToken() {
  const input = document.getElementById("settingsMsToken");
  const token = input?.value?.trim();
  const result = document.getElementById("settingsResult");
  if (!token || token === "********") {
    result.innerHTML = '<span style="color:var(--warning)">Paste the ms_token value first</span>';
    return;
  }
  const btn = document.getElementById("btnSaveToken");
  setButtonLoading(btn, true);
  try {
    const r = await API.postJson("/api/settings/tiktok-api/token", { ms_token: token });
    result.innerHTML = `<span style="color:var(--success)">${escapeHtml(r.message)}</span>`;
    toast(r.message);
    refreshSettings();
  } catch (e) {
    result.innerHTML = `<span style="color:var(--danger)">${escapeHtml(e.message)}</span>`;
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function testTikTokApi() {
  const result = document.getElementById("settingsResult");
  result.innerHTML = '<span style="color:var(--text-muted)">Testing connection...</span>';
  const btn = document.getElementById("btnTestApi");
  setButtonLoading(btn, true);
  try {
    const r = await API.post("/api/settings/tiktok-api/test");
    if (r.success) {
      result.innerHTML = `<span style="color:var(--success)">${escapeHtml(r.message)}</span>`;
      toast("TikTok API connected");
    } else {
      result.innerHTML = `<span style="color:var(--warning)">${escapeHtml(r.message)}</span>`;
      toast(r.message, "error");
    }
  } catch (e) {
    result.innerHTML = `<span style="color:var(--danger)">${escapeHtml(e.message)}</span>`;
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

function updateNavBadges({ accounts, proxies, alerts }) {
  const set = (id, n) => {
    const el = document.getElementById(id);
    if (el == null || n == null) return;
    el.textContent = String(n);
    el.dataset.zero = n === 0 ? "true" : "false";
  };
  if (accounts != null) set("navBadgeAccounts", accounts);
  if (proxies != null) set("navBadgeProxies", proxies);
  if (alerts != null) set("navBadgeAlerts", alerts);
}

function setActiveNav(sectionId) {
  const link = document.querySelector(`.nav-link[data-section="${sectionId}"]`);
  if (!link) return;

  document.querySelectorAll(".nav-link").forEach((a) => a.classList.remove("active"));
  link.classList.add("active");

  const title = document.getElementById("headerPageTitle");
  const subtitle = document.getElementById("headerPageSubtitle");
  if (title) title.textContent = link.dataset.title || sectionId;
  if (subtitle) subtitle.textContent = link.dataset.subtitle || "";
}

function closeMobileSidebar() {
  document.getElementById("layout")?.classList.remove("sidebar-open");
}

function initNav() {
  document.querySelectorAll(".nav-link[data-section]").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const id = link.getAttribute("data-section");
      document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
      setActiveNav(id);
      closeMobileSidebar();
    });
  });

  const layout = document.getElementById("layout");
  document.getElementById("menuToggle")?.addEventListener("click", () => {
    layout?.classList.toggle("sidebar-open");
  });
  document.getElementById("sidebarBackdrop")?.addEventListener("click", closeMobileSidebar);

  const collapseBtn = document.getElementById("sidebarCollapse");
  collapseBtn?.addEventListener("click", () => {
    const collapsed = layout?.classList.toggle("sidebar-collapsed");
    const icon = collapseBtn.querySelector("[data-lucide]");
    if (icon) {
      icon.setAttribute(
        "data-lucide",
        collapsed ? "panel-left-open" : "panel-left-close"
      );
      if (typeof lucide !== "undefined") lucide.createIcons();
    }
  });

  const sections = ["overview", "accounts", "proxies", "alerts", "settings"];
  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (visible?.target?.id) setActiveNav(visible.target.id);
    },
    { rootMargin: "-45% 0px -45% 0px", threshold: [0, 0.25, 0.5] }
  );
  sections.forEach((id) => {
    const el = document.getElementById(id);
    if (el) observer.observe(el);
  });

  setActiveNav("overview");
}

function openModal(id) {
  const el = document.getElementById(id);
  if (el) {
    el.classList.add("open");
    if (typeof lucide !== "undefined") lucide.createIcons();
  }
}

function closeModal(id) {
  document.getElementById(id)?.classList.remove("open");
}

function setupModalTabs(modalId, kind) {
  const modal = document.getElementById(modalId);
  if (!modal) return;
  modal.querySelectorAll(".modal-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const name = tab.dataset.tab;
      modal.querySelectorAll(".modal-tabs .tab").forEach((t) => t.classList.toggle("active", t === tab));
      modal.querySelectorAll(".tab-panel").forEach((p) => {
        p.hidden = p.id !== name;
      });
      if (kind === "account") accountModalTab = name;
      else proxyModalTab = name;
    });
  });
}

function showImportResult(elId, result) {
  const el = document.getElementById(elId);
  if (!el) return;
  const errCount = (result.errors || []).length;
  el.className = "import-result show" + (errCount ? " has-errors" : "");
  el.innerHTML = `
    <strong>Import complete</strong><br>
    Imported: ${result.imported ?? 0} · Skipped: ${result.skipped ?? 0} · Failed: ${result.failed ?? 0}
    ${result.total_rows != null ? `<br>Rows in file: ${result.total_rows}` : ""}
    ${errCount ? `<br><span style="color:var(--warning)">${errCount} row error(s)</span>` : ""}`;
}

async function submitAccountModal() {
  const btn = document.getElementById("btnSubmitAccount");
  if (btn) btn.disabled = true;
  try {
    if (accountModalTab === "account-bulk") {
      const file = document.getElementById("accCsvFile")?.files?.[0];
      const csvText = document.getElementById("accCsvText")?.value?.trim() || "";
      const skip = document.getElementById("accSkipExisting")?.checked ?? true;
      let result;
      if (file) {
        const fd = new FormData();
        fd.append("file", file);
        result = await API.postFormData(`/api/accounts/import?skip_existing=${skip}`, fd);
      } else if (csvText) {
        result = await API.postJson("/api/accounts/import", { csv_text: csvText, skip_existing: skip });
      } else {
        toast("Choose a CSV file or paste CSV text", "error");
        return;
      }
      showImportResult("accountImportResult", result);
      toast(`Imported ${result.imported} accounts`);
      await refreshAll();
    } else {
      const username = document.getElementById("accUsername")?.value?.trim();
      if (!username) {
        toast("Username is required", "error");
        return;
      }
      await API.postJson("/api/accounts", {
        username,
        proxy_id: parseInt(document.getElementById("accProxyId")?.value || "0", 10),
        password: document.getElementById("accPassword")?.value || "",
        notes: document.getElementById("accNotes")?.value || "",
        status: document.getElementById("accStatus")?.value || "pending",
      });
      toast(`Account @${username} created`);
      closeModal("modalAccount");
      await refreshAll();
    }
  } catch (e) {
    toast(e.message, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function submitProxyModal() {
  const btn = document.getElementById("btnSubmitProxy");
  if (btn) btn.disabled = true;
  try {
    if (proxyModalTab === "proxy-bulk") {
      const file = document.getElementById("pxCsvFile")?.files?.[0];
      const csvText = document.getElementById("pxCsvText")?.value?.trim() || "";
      const merge = document.getElementById("pxMerge")?.checked ?? true;
      let result;
      if (file) {
        const fd = new FormData();
        fd.append("file", file);
        result = await API.postFormData(`/api/proxies/import?merge=${merge}`, fd);
      } else if (csvText) {
        result = await API.postJson("/api/proxies/import", { csv_text: csvText, merge });
      } else {
        toast("Choose a CSV file or paste CSV text", "error");
        return;
      }
      showImportResult("proxyImportResult", result);
      toast(`Imported ${result.imported} proxies`);
      await refreshAll();
    } else {
      const ip = document.getElementById("pxIp")?.value?.trim();
      const port = parseInt(document.getElementById("pxPort")?.value || "0", 10);
      if (!ip || !port) {
        toast("IP and port are required", "error");
        return;
      }
      await API.postJson("/api/proxies", {
        ip,
        port,
        protocol: document.getElementById("pxProtocol")?.value || "http",
        username: document.getElementById("pxUser")?.value || "",
        password: document.getElementById("pxPass")?.value || "",
        status: "active",
      });
      toast("Proxy added");
      closeModal("modalProxy");
      await refreshAll();
    }
  } catch (e) {
    toast(e.message, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function openAccountModal(tab = "account-single") {
  document.getElementById("accountImportResult")?.classList.remove("show");
  const preview = document.getElementById("accountProfilePreview");
  if (preview) {
    preview.hidden = true;
    preview.innerHTML = "";
  }
  accountModalTab = tab;
  document.querySelector(`#modalAccount .tab[data-tab=${tab}]`)?.click();
  openModal("modalAccount");
}

function openProxyModal(tab = "proxy-single") {
  document.getElementById("proxyImportResult")?.classList.remove("show");
  proxyModalTab = tab;
  document.querySelector(`#modalProxy .tab[data-tab=${tab}]`)?.click();
  openModal("modalProxy");
}

function initModals() {
  setupModalTabs("modalAccount", "account");
  setupModalTabs("modalProxy", "proxy");

  document.getElementById("btnAddAccount")?.addEventListener("click", () => openAccountModal());
  document.getElementById("navAddAccount")?.addEventListener("click", () => openAccountModal());
  document.getElementById("btnImportAccounts")?.addEventListener("click", () =>
    openAccountModal("account-bulk")
  );
  document.getElementById("navImportAccounts")?.addEventListener("click", () =>
    openAccountModal("account-bulk")
  );
  document.getElementById("btnAddProxy")?.addEventListener("click", () => openProxyModal());
  document.getElementById("navAddProxy")?.addEventListener("click", () => openProxyModal());
  document.getElementById("btnImportProxies")?.addEventListener("click", () => {
    document.getElementById("proxyImportResult")?.classList.remove("show");
    proxyModalTab = "proxy-bulk";
    document.querySelector("#modalProxy .tab[data-tab=proxy-bulk]")?.click();
    openModal("modalProxy");
  });

  document.getElementById("btnSubmitAccount")?.addEventListener("click", submitAccountModal);
  document.getElementById("btnSubmitProxy")?.addEventListener("click", submitProxyModal);
  document.getElementById("btnLookupProfile")?.addEventListener("click", lookupTikTokProfile);
  document.getElementById("btnSyncAllProfiles")?.addEventListener("click", syncAllAccountProfiles);

  // Action buttons (Farm, Post, Sync, Check, Del) — event delegation
  document.getElementById("accountsBody")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    e.preventDefault();
    const action = btn.dataset.action;
    const id = parseInt(btn.dataset.id);
    if (action === "delete") {
      deleteAccount(id);
    } else {
      runAction(action, id);
    }
  });
  document.getElementById("accountsBody")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-delete-proxy]");
    if (!btn) return;
    e.preventDefault();
    deleteProxy(parseInt(btn.dataset.deleteProxy));
  });

  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => closeModal(btn.dataset.close));
  });
  document.querySelectorAll(".modal-backdrop").forEach((backdrop) => {
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) backdrop.classList.remove("open");
    });
  });

  document.getElementById("accountSearch")?.addEventListener("input", () => {
    accountPage = 1;
    renderAccountsTable();
  });
  document.getElementById("proxySearch")?.addEventListener("input", () => {
    proxyPage = 1;
    renderProxiesTable();
  });
}

function init() {
  if (typeof lucide !== "undefined") lucide.createIcons();
  initNav();
  initModals();
  document.getElementById("btnRefresh")?.addEventListener("click", refreshAll);
  document.getElementById("btnProxyCheck")?.addEventListener("click", checkAllProxies);
  document.getElementById("btnReschedule")?.addEventListener("click", rescheduleJobs);

  refreshAll();
  refreshTimer = setInterval(refreshAll, 60000);
}

document.addEventListener("DOMContentLoaded", init);
