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
      if (d && typeof d === "object" && d.error_type) {
        const err = new Error(d.error || "Request failed");
        err.errorType = d.error_type;
        err.profilePayload = d;
        throw err;
      }
      return JSON.stringify(d);
    } catch (e) {
      if (e && e.errorType) throw e;
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
let postsCache = [];
let liveWs = null;
let liveSessionMeta = { type: "farm", accountId: null, duration: 15 };
let accountLogsWs = null;
let accountLogsAccountId = null;
let accountLogsFilter = "";
let accountLogsEntries = [];

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

function proxyLabel(proxyId) {
  if (!proxyId) return "—";
  const p = proxiesCache.find((x) => x.id === proxyId);
  if (p) return `${proxyId}: ${p.ip}`;
  return String(proxyId);
}

function populateProxySelects() {
  const options =
    '<option value="0">— None / auto —</option>' +
    proxiesCache
      .map(
        (p) =>
          `<option value="${p.id}">${escapeHtml(p.ip)}:${p.port} (#${p.id})</option>`
      )
      .join("");
  ["sellerImportProxyId", "accSellerProxyId", "accProxyId"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    const prev = el.value;
    if (el.tagName === "SELECT") {
      el.innerHTML = options;
      if (prev) el.value = prev;
    }
  });
}

function cookieBadgeHtml(account) {
  const cs = account.cookie_status || {};
  const n = cs.cookie_count || 0;
  if (!cs.has_cookies) {
    return '<span class="cookie-badge none">🍪 0</span>';
  }
  if (cs.expired || !cs.has_sessionid) {
    return `<span class="cookie-badge warn" title="No sessionid">🍪 ${n}</span>`;
  }
  return `<span class="cookie-badge ok" title="sessionid OK">🍪 ${n} OK</span>`;
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
      refreshAffiliate(),
      refreshSettings(),
      refreshPosts(),
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
      (a.display_name || "").toLowerCase().includes(q) ||
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
        <p style="margin-top:0.35rem;font-size:0.75rem;color:var(--text-dim)">After adding, use <strong>Lookup</strong> or <strong>Sync</strong> to pull followers &amp; posts (browser scanner, no API token)</p>
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
          <th>Proxy</th>
          <th>Status</th>
          <th>Cookies</th>
          <th>Followers</th>
          <th>Posts</th>
          <th>Last active</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        ${slice
          .map((a) => {
            const dn = (a.display_name || "").trim();
            const primary = dn || `@${a.username}`;
            const secondary = dn
              ? `@${escapeHtml(a.username)} · ID ${a.id}`
              : `ID ${a.id}`;
            return `
          <tr>
            <td>
              <div class="user-cell">
                <div class="avatar">${initials(dn || a.username)}</div>
                <div>
                  <button type="button" class="link-btn" data-view-account="${a.id}">
                    <strong>${escapeHtml(primary)}</strong>
                  </button>
                  <div style="font-size:0.7rem;color:var(--text-dim)">${secondary}</div>
                </div>
              </div>
            </td>
            <td class="cell-mono" title="Proxy ID">${escapeHtml(proxyLabel(a.proxy_id))}</td>
            <td><span class="badge ${BADGE_CLASS[a.status] || "badge-pending"}">${a.status}</span></td>
            <td>${cookieBadgeHtml(a)}</td>
            <td style="font-family:var(--font-mono)">${formatNum(a.followers)}</td>
            <td style="font-family:var(--font-mono)">${a.total_posts || 0}</td>
            <td style="font-size:0.8rem;color:var(--text-muted)">${formatDate(a.last_active)}</td>
            <td>
              <div class="actions">
                <button class="btn btn-sm" type="button" data-action="farm" data-id="${a.id}">Farm</button>
                <div class="dropdown actions-dropdown">
                  <button class="btn btn-sm dropdown-toggle" type="button" data-action="post-menu" data-id="${a.id}">Post ▼</button>
                  <div class="dropdown-menu">
                    <button type="button" data-action="compose" data-id="${a.id}">Compose</button>
                    <button type="button" data-action="batch-schedule" data-id="${a.id}">Batch schedule</button>
                    <button type="button" data-action="quick-post" data-id="${a.id}">Quick Post</button>
                  </div>
                </div>
                <button class="btn btn-sm" type="button" data-action="sync" data-id="${a.id}" title="Sync TikTok profile">Sync</button>
                <button class="btn btn-sm" type="button" data-action="logs" data-id="${a.id}" title="Account logs">Logs</button>
                <button class="btn btn-sm" type="button" data-action="check" data-id="${a.id}" title="Health check: verify TikTok login with saved cookies">Check</button>
                <button class="btn btn-sm btn-danger" type="button" data-action="delete" data-id="${a.id}" title="Delete account permanently">Delete</button>
              </div>
            </td>
          </tr>`;
          })
          .join("")}
      </tbody>
    </table>`;

  renderPagination("accountsMeta", page, pages, total, (p) => {
    accountPage = p;
    renderAccountsTable();
  });

  wrap.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const id = parseInt(btn.dataset.id, 10);
      const action = btn.dataset.action;
      if (action === "post-menu") {
        e.stopPropagation();
        const dd = btn.closest(".actions-dropdown");
        document.querySelectorAll(".actions-dropdown.open").forEach((d) => {
          if (d !== dd) d.classList.remove("open");
        });
        dd?.classList.toggle("open");
        return;
      }
      document.querySelectorAll(".actions-dropdown.open").forEach((d) => d.classList.remove("open"));
      if (action === "delete") deleteAccount(id);
      else if (action === "compose") openPostComposer(id);
      else if (action === "batch-schedule") openBatchSchedule(id);
      else if (action === "quick-post") quickPost(id);
      else if (action === "logs") openLogPanel(id);
      else runAction(action, id);
    });
  });
  wrap.querySelectorAll("[data-view-account]").forEach((btn) => {
    btn.addEventListener("click", () => openAccountDetail(parseInt(btn.dataset.viewAccount, 10)));
  });
  if (typeof lucide !== "undefined") lucide.createIcons();
}

async function openAccountDetail(accountId) {
  const body = document.getElementById("accountDetailBody");
  const title = document.getElementById("accountDetailTitle");
  if (!body) return;
  openModal("modalAccountDetail");
  body.innerHTML = '<div class="empty-state">Loading…</div>';
  try {
    const r = await API.get(`/api/accounts/${accountId}`);
    const a = r.account;
    if (title) {
      const dn = (a.display_name || "").trim();
      title.textContent = dn ? `${dn} (@${a.username})` : `@${a.username}`;
    }
    const cs = a.cookie_status || {};
    body.innerHTML = `
      <div class="account-detail-grid">
        <div class="account-detail-section">
          <h4>Account</h4>
          <p>ID ${a.id} · Proxy ${a.proxy_id || "—"} · <span class="badge ${BADGE_CLASS[a.status] || ""}">${a.status}</span></p>
          <p style="font-size:0.8rem;color:var(--text-dim)">${escapeHtml(a.notes || "—")}</p>
        </div>
        <div class="account-detail-section">
          <h4>Cookies</h4>
          <p>${cookieBadgeHtml(a)} ${cs.has_sessionid ? "· sessionid present" : cs.has_cookies ? "· missing sessionid" : ""}</p>
          <textarea id="detailCookieInput" rows="4" placeholder="Paste cookie string: name=value; ..."></textarea>
          <div class="actions" style="margin-top:0.5rem">
            <button type="button" class="btn btn-sm btn-primary" id="btnDetailSaveCookies">Update cookies</button>
            <button type="button" class="btn btn-sm" id="btnDetailDeleteCookies">Delete cookies</button>
          </div>
        </div>
      </div>`;
    document.getElementById("btnDetailSaveCookies")?.addEventListener("click", async () => {
      const val = document.getElementById("detailCookieInput")?.value?.trim();
      if (!val) {
        toast("Paste cookie string first", "error");
        return;
      }
      try {
        await API.postJson(`/api/accounts/${accountId}/cookies`, { cookie_data: val });
        toast("Cookies updated");
        closeModal("modalAccountDetail");
        refreshAccounts();
      } catch (e) {
        toast(e.message, "error");
      }
    });
    document.getElementById("btnDetailDeleteCookies")?.addEventListener("click", async () => {
      if (!confirm("Clear cookies for this account?")) return;
      try {
        await API.delete(`/api/accounts/${accountId}/cookies`);
        toast("Cookies cleared");
        closeModal("modalAccountDetail");
        refreshAccounts();
      } catch (e) {
        toast(e.message, "error");
      }
    });
    if (typeof lucide !== "undefined") lucide.createIcons();
  } catch (e) {
    body.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
  }
}

async function submitSellerImport(resultElId = "sellerImportResult") {
  const fromPanel = document.getElementById("sellerImportText")?.value?.trim();
  const fromModal = document.getElementById("accSellerText")?.value?.trim();
  const text = resultElId === "accountImportResult" ? fromModal || fromPanel : fromPanel || fromModal;

  const proxyEl =
    resultElId === "accountImportResult"
      ? document.getElementById("accSellerProxyId") || document.getElementById("sellerImportProxyId")
      : document.getElementById("sellerImportProxyId") || document.getElementById("accSellerProxyId");
  const proxyId = parseInt(proxyEl?.value || "0", 10);

  const skipEl =
    resultElId === "accountImportResult"
      ? document.getElementById("accSellerSkipExisting") || document.getElementById("sellerSkipExisting")
      : document.getElementById("sellerSkipExisting") || document.getElementById("accSellerSkipExisting");
  const skip = skipEl?.checked ?? true;

  if (!text) {
    toast("Paste seller format accounts first", "error");
    return;
  }

  const btn = document.getElementById("btnSellerImport");
  setButtonLoading(btn, true);
  try {
    const autoProxy = document.getElementById("sellerAutoProxy")?.checked ?? false;
    const r = await API.postJson("/api/accounts/import/seller", {
      accounts: text,
      proxy_id: proxyId,
      skip_existing: skip,
      require_cookies: true,
      auto_assign_proxy: autoProxy,
    });
    showImportResult(resultElId, r);
    const errN = (r.parse_errors || []).length;
    toast(`Imported ${r.imported || 0}${errN ? `, ${errN} line errors` : ""}`);
    await refreshAll();
  } catch (e) {
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
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
  populateProxySelects();
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
          <th>Server</th>
          <th>Port</th>
          <th>Protocol</th>
          <th>Status</th>
          <th>Accounts</th>
          <th>Fails</th>
          <th>Checked</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        ${slice
          .map((p) => {
            const protocol = (p.protocol || "http").toUpperCase();
            const checked = formatDate(p.last_checked);
            const live = p.status === "active";
            const usedList = p.used_by || [];
            const used = usedList.length
              ? usedList
                  .map((u) => {
                    const name = `@${escapeHtml(u)}`;
                    return live ? name : `${name} <span class="proxy-assign-warn" title="Account on non-live proxy">⚠</span>`;
                  })
                  .join(", ")
              : "—";
            return `
          <tr>
            <td class="cell-mono">${p.id}</td>
            <td class="cell-mono proxy-ip">${escapeHtml(p.ip)}</td>
            <td class="cell-mono">${p.port}</td>
            <td class="cell-mono">${protocol}</td>
            <td><span class="${live ? "proxy-status-live" : "proxy-status-dead"}">${live ? "Live" : escapeHtml(p.status || "inactive")}</span></td>
            <td style="font-size:0.8rem">${used}</td>
            <td class="cell-mono">${p.fail_count || 0}</td>
            <td class="cell-dim">${escapeHtml(checked)}</td>
            <td>
              <button class="btn btn-sm" type="button" data-check-proxy="${p.id}">Check</button>
              <button class="btn btn-sm" type="button" data-delete-proxy="${p.id}">Del</button>
            </td>
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
  wrap.querySelectorAll("[data-check-proxy]").forEach((btn) => {
    btn.addEventListener("click", () => checkSingleProxy(parseInt(btn.dataset.checkProxy, 10), btn));
  });
  if (typeof lucide !== "undefined") lucide.createIcons();
}

function confirmDeleteAccount(id) {
  const acc = accountsCache.find((a) => a.id === id);
  const label = acc ? `@${acc.username} (ID ${id})` : `account #${id}`;
  return confirm(
    `Delete ${label} permanently?\n\nThis removes the account, cookies, and scheduled jobs. This cannot be undone.`
  );
}

async function deleteAccount(id) {
  if (!confirmDeleteAccount(id)) return;
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
  const name = profile.display_name || profile.nickname || profile.username;
  const posts = profile.total_posts ?? profile.video_count ?? 0;
  const likes = profile.likes ?? profile.heart_count ?? 0;
  const profileUrl =
    profile.profile_url || `https://www.tiktok.com/@${profile.username || ""}`;
  el.innerHTML = `
    <strong>@${escapeHtml(profile.username)}</strong>
    ${profile.verified ? ' <span class="badge badge-active">verified</span>' : ""}
    ${profile.private_account ? ' <span class="badge badge-pending">private</span>' : ""}
    <div>${escapeHtml(name)}</div>
    ${profile.bio ? `<div style="color:var(--text-dim);margin-top:0.25rem">${escapeHtml(profile.bio)}</div>` : ""}
    <div class="profile-stats">
      <span>${formatNum(profile.followers)} followers</span>
      <span>${formatNum(profile.following)} following</span>
      <span>${posts} videos</span>
      <span>${formatNum(likes)} likes</span>
    </div>
    <a href="${escapeHtml(profileUrl)}" target="_blank" rel="noopener" style="font-size:0.75rem;margin-top:0.35rem;display:inline-block">Open on TikTok</a>
  `;
}

function profileErrorToast(result, username) {
  const t = result?.error_type;
  const u = username || result?.username || "";
  const messages = {
    timeout: "⏱ Timeout — TikTok không phản hồi. Kiểm tra proxy hoặc thử lại.",
    blocked: "🚫 TikTok chặn request từ IP này. Thử đổi proxy.",
    not_found: `❌ @${u} — TikTok không tìm thấy profile công khai. Kiểm tra username đúng (không phải UID seller).`,
    login_required: `🔑 @${u} — cookies hết hạn, TikTok bắt đăng nhập lại. Dán cookies mới rồi Sync.`,
    private: `🔒 Account @${u} đang ở chế độ riêng tư.`,
    captcha: "🤖 TikTok yêu cầu captcha. Thử lại sau 30 giây.",
  };
  return messages[t] || result?.error || "Profile scan failed";
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
    const r = await API.post(`/api/profile/scan/${encodeURIComponent(username)}`);
    clearToast(toastEl);
    if (!r.success) {
      toast(profileErrorToast(r, username), "error");
      if (preview) {
        preview.hidden = false;
        preview.innerHTML = `<span style="color:var(--danger)">${escapeHtml(r.error || "Scan failed")}</span>`;
      }
      return;
    }
    renderProfilePreview(preview, r);
    toast(`@${r.username} \u2014 ${formatNum(r.followers)} followers`);
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
    const p = r.profile || r;
    if (p.private_account) {
      toast(profileErrorToast(p, p.username), "error");
    } else {
      const dn = (p.display_name || "").trim();
      const label = dn ? `${dn} (@${p.username || accountId})` : `@${p.username || accountId}`;
      toast(`${label}: ${formatNum(p.followers)} followers`);
    }
    refreshAccounts();
    refreshStats();
  } catch (e) {
    clearToast(toastEl);
    const msg = e.profilePayload
      ? profileErrorToast(e.profilePayload, e.profilePayload.username)
      : e.message;
    toast(msg, "error");
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

function wsUrl(path) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

function showLiveLogPanel(title, accountId) {
  const panel = document.getElementById("liveLogPanel");
  if (!panel) return;
  panel.hidden = false;
  document.getElementById("liveLogTitle").textContent = title;
  document.getElementById("liveLogAccount").textContent = accountId
    ? `Account #${accountId}`
    : "";
  document.getElementById("liveLogBody").innerHTML = "";
  document.getElementById("liveLogFoot").textContent = "Connecting…";
  document.getElementById("liveProgressBar").style.width = "0%";
}

function hideLiveLogPanel() {
  if (liveWs) {
    liveWs.close();
    liveWs = null;
  }
  const panel = document.getElementById("liveLogPanel");
  if (panel) panel.hidden = true;
}

function appendLiveLog(line, cls = "") {
  const body = document.getElementById("liveLogBody");
  if (!body) return;
  const el = document.createElement("div");
  el.className = `live-log-line ${cls}`;
  el.textContent = line;
  body.appendChild(el);
  body.scrollTop = body.scrollHeight;
}

function handleLiveEvent(ev) {
  const t = ev.type || "";
  const d = ev.data || {};
  if (t === "farm:ping" || t === "post:ping") {
    const foot = document.getElementById("liveLogFoot");
    if (foot && !foot.textContent?.startsWith("Done")) {
      foot.textContent = "Running…";
    }
    return;
  }

  if (t === "farm:start" || t === "post:start") {
    liveSessionMeta.duration = d.duration || liveSessionMeta.duration || 15;
    appendLiveLog(`▶ ${t}`, "ok");
    return;
  }
  if (t === "farm:log" || t === "post:log") {
    appendLiveLog(d.message || JSON.stringify(d));
    return;
  }
  if (t === "farm:progress") {
    const pct = liveSessionMeta.duration
      ? Math.min(100, Math.round(((d.elapsed_sec || 0) / (liveSessionMeta.duration * 60)) * 100))
      : 0;
    document.getElementById("liveProgressBar").style.width = `${pct}%`;
    const phase = d.phase ? `${d.phase}: ` : "";
    const suffix = d.done ? " ✓" : "";
    appendLiveLog(
      `⏱ ${phase}${d.elapsed_sec || 0}s · scrolls ${d.scrolls || 0} · videos ${d.videos_seen || 0}${suffix}`
    );
    document.getElementById("liveLogFoot").textContent =
      `Running · ${d.elapsed_sec || 0}s · ${d.scrolls || 0} scrolls`;
    return;
  }
  if (t === "farm:action" || t === "post:action") {
    appendLiveLog(`${d.action || "action"}: ${d.status || "?"}`);
    return;
  }
  if (t === "farm:error" || t === "post:error") {
    appendLiveLog(`✗ ${d.message || "Error"}`, "error");
    document.getElementById("liveLogFoot").textContent = "Error";
    return;
  }
  if (t === "farm:complete") {
    const stats = d.stats || {};
    document.getElementById("liveProgressBar").style.width = "100%";
    document.getElementById("liveLogFoot").textContent = d.success
      ? `Done · ${d.duration || 0}s · likes ${stats.likes || 0}`
      : "Session failed";
    appendLiveLog(`✓ Farm complete (${d.duration || 0}s)`, d.success ? "ok" : "error");
    setTimeout(refreshAll, 1500);
    return;
  }
  if (t === "post:complete") {
    document.getElementById("liveProgressBar").style.width = "100%";
    const ok = d.success;
    document.getElementById("liveLogFoot").textContent = ok ? "Posted" : "Post failed";
    appendLiveLog(ok ? "✓ Post uploaded" : "✗ Post failed", ok ? "ok" : "error");
    setTimeout(refreshPosts, 1500);
    return;
  }
  appendLiveLog(`${t} ${JSON.stringify(d)}`);
}

function connectLiveWs(sessionId, meta = {}) {
  if (liveWs) liveWs.close();
  liveSessionMeta = { ...liveSessionMeta, ...meta };
  const url = wsUrl(`/api/ws/${sessionId}`);
  liveWs = new WebSocket(url);
  liveWs.onopen = () => {
    document.getElementById("liveLogFoot").textContent = "Connected";
    appendLiveLog("WebSocket connected", "ok");
  };
  liveWs.onmessage = (msg) => {
    try {
      handleLiveEvent(JSON.parse(msg.data));
    } catch {
      appendLiveLog(msg.data);
    }
  };
  liveWs.onerror = () => {
    document.getElementById("liveLogFoot").textContent = "Connection error";
  };
  liveWs.onclose = () => {
    liveWs = null;
  };
}

async function runFarmAction(accountId) {
  const btn = document.querySelector(`[data-action="farm"][data-id="${accountId}"]`);
  setButtonLoading(btn, true);
  const toastEl = toast(`Starting farm for account ${accountId}…`, "loading");
  try {
    const result = await API.post(`/api/actions/farm/${accountId}`);
    clearToast(toastEl);
    if (result.session_id) {
      showLiveLogPanel("Farm session", accountId);
      connectLiveWs(result.session_id, { type: "farm", accountId, duration: 15 });
      toast(result.message || "Farm started — watch live log");
    } else {
      toast(result.message || "Farm started");
    }
  } catch (e) {
    clearToast(toastEl);
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function quickPost(accountId) {
  const acc = accountsCache.find((a) => a.id === accountId);
  const toastEl = toast(`Quick post for @${acc?.username || accountId}…`, "loading");
  try {
    const result = await API.postJson(`/api/actions/post/${accountId}`, {
      caption: "Check this out! 🔥",
      hashtags: "fyp foryou viral",
    });
    clearToast(toastEl);
    if (result.session_id) {
      showLiveLogPanel("Post upload", accountId);
      connectLiveWs(result.session_id, { type: "post", accountId });
      toast("Post started — watch live log");
    } else {
      toast(result.message || "Post completed");
      refreshPosts();
    }
  } catch (e) {
    clearToast(toastEl);
    toast(e.message, "error");
  }
}

async function runAction(action, accountId) {
  if (action === "sync") return syncAccountProfile(accountId);
  if (action === "farm") return runFarmAction(accountId);

  const btn = document.querySelector(`[data-action="${action}"][data-id="${accountId}"]`);
  setButtonLoading(btn, true);
  const paths = { check: `/api/actions/check/${accountId}` };
  const toastEl = toast(
    action === "check" ? "Health check: opening TikTok with cookies…" : `Starting ${action}…`,
    "loading"
  );
  try {
    const result = await API.post(paths[action]);
    clearToast(toastEl);
    if (action === "check") {
      const ok = result.ok ?? result.result?.login?.logged_in;
      toast(result.message || (ok ? "Login OK" : "Login failed"), ok ? "success" : "error");
    } else {
      toast(result.message || `${action} completed`);
    }
    setTimeout(refreshAll, 2000);
  } catch (e) {
    clearToast(toastEl);
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function refreshPosts() {
  const wrap = document.getElementById("postsTable");
  if (!wrap) return;
  const filterEl = document.getElementById("postsFilterAccount");
  const accountId = filterEl?.value ? parseInt(filterEl.value, 10) : null;
  const q = accountId ? `?account_id=${accountId}&limit=50` : "?limit=50";
  try {
    const data = await API.get(`/api/posts${q}`);
    postsCache = data.posts || [];
    renderPostsTable();
    const badge = document.getElementById("navBadgePosts");
    if (badge) badge.textContent = String(postsCache.length);
    populatePostsFilter();
  } catch (e) {
    wrap.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
  }
}

function populatePostsFilter() {
  const sel = document.getElementById("postsFilterAccount");
  if (!sel || sel.dataset.filled === "1") return;
  sel.dataset.filled = "1";
  accountsCache.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = String(a.id);
    opt.textContent = `@${a.username}`;
    sel.appendChild(opt);
  });
}

function renderPostsTable() {
  const wrap = document.getElementById("postsTable");
  if (!wrap) return;
  if (!postsCache.length) {
    wrap.innerHTML = `<div class="empty-state">No posts yet — use Compose or Quick Post on an account</div>`;
    return;
  }
  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Account</th>
          <th>Status</th>
          <th>Caption</th>
          <th>Views</th>
          <th>Posted</th>
        </tr>
      </thead>
      <tbody>
        ${postsCache
          .map(
            (p) => `
          <tr>
            <td>@${escapeHtml(p.username || p.account_id)}</td>
            <td><span class="badge ${p.status === "posted" ? "badge-active" : "badge-pending"}">${escapeHtml(p.status || "pending")}</span></td>
            <td style="max-width:14rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(p.caption || "")}">${escapeHtml((p.caption || "").slice(0, 60))}</td>
            <td style="font-family:var(--font-mono)">${formatNum(p.views || 0)}</td>
            <td style="font-size:0.8rem;color:var(--text-muted)">${formatDate(p.posted_at || p.scheduled_at)}</td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

async function loadPostTemplates() {
  try {
    const data = await API.get("/api/post-templates");
    const sel = document.getElementById("postTemplate");
    if (!sel || !data.templates?.length) return;
    sel.innerHTML = data.templates
      .map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`)
      .join("");
  } catch {
    /* optional */
  }
}

function updatePostTypeRows() {
  const isVideo = document.querySelector('input[name="postType"]:checked')?.value === "video";
  document.getElementById("postSlideshowFilesRow").hidden = isVideo;
  document.getElementById("postVideoFileRow").hidden = !isVideo;
  document.getElementById("btnPostGenerate").hidden = isVideo;
}

function previewSlideshowFiles(files) {
  const strip = document.getElementById("postPreviewStrip");
  const list = Array.from(files || []);
  if (!list.length) {
    strip.innerHTML = '<p class="form-hint">Click Generate to preview auto slideshow, or choose images above</p>';
    return;
  }
  strip.innerHTML = list
    .map((file) => {
      const url = URL.createObjectURL(file);
      return `<img src="${url}" alt="${file.name}" loading="lazy">`;
    })
    .join("");
}

function openPostComposer(accountId) {
  const acc = accountsCache.find((a) => a.id === accountId);
  document.getElementById("postComposerAccountId").value = String(accountId);
  document.getElementById("postComposerTitle").textContent = `Compose — @${acc?.username || accountId}`;
  document.getElementById("postCaption").value = "";
  document.getElementById("postHashtags").value = "fyp foryou viral";
  document.getElementById("postAffiliateLink").value = "";
  document.getElementById("postImagesDir").value = "";
  const slideshowInput = document.getElementById("postSlideshowFiles");
  const videoInput = document.getElementById("postVideoFile");
  if (slideshowInput) slideshowInput.value = "";
  if (videoInput) videoInput.value = "";
  document.getElementById("postPreviewStrip").innerHTML =
    '<p class="form-hint">Choose images, click Generate for auto slideshow, or pick a video</p>';
  document.querySelector('input[name="postType"][value="slideshow"]').checked = true;
  updatePostTypeRows();
  loadPostTemplates();
  openModal("modalPostComposer");
}

const BATCH_VIDEO_RE = /\.(mp4|mov|webm|mkv)$/i;
let batchFolderVideoFiles = [];
let batchCurrentStep = 1;
const BATCH_STEP_COUNT = 4;

function filterBatchVideoFiles(fileList) {
  return Array.from(fileList || [])
    .filter((f) => BATCH_VIDEO_RE.test(f.name))
    .sort((a, b) =>
      (a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name, undefined, {
        sensitivity: "base",
      })
    );
}

function renderBatchFilePreview(files, targetId, headerHtml = "") {
  const el = document.getElementById(targetId);
  if (!el) return;
  if (!files.length) {
    el.innerHTML = headerHtml || '<p class="form-hint">No videos selected yet.</p>';
    return;
  }
  const show = files.slice(0, 30);
  const more = files.length - show.length;
  el.innerHTML =
    headerHtml +
    `<ul class="batch-file-list">${show
      .map((f) => `<li>${escapeHtml(f.webkitRelativePath || f.name)}</li>`)
      .join("")}${more > 0 ? `<li class="form-hint">…and ${more} more</li>` : ""}</ul>`;
}

function updateBatchSourceRows() {
  const useFolder = document.querySelector('input[name="batchSource"]:checked')?.value === "folder";
  document.getElementById("batchFolderRow").hidden = !useFolder;
  document.getElementById("batchUploadRow").hidden = useFolder;
  updateBatchStep2Preview();
  if (batchCurrentStep === 4) updateBatchReviewSummary();
}

function getBatchSourceMeta() {
  const useFolder = document.querySelector('input[name="batchSource"]:checked')?.value === "folder";
  const serverPath = document.getElementById("batchFolderPath")?.value?.trim() || "";
  if (useFolder) {
    if (serverPath) {
      return { mode: "server", count: null, label: serverPath };
    }
    const n = batchFolderVideoFiles.length;
    const root =
      batchFolderVideoFiles[0]?.webkitRelativePath?.split("/")[0] || "folder";
    return { mode: "folder", count: n, label: n ? `${n} videos — ${root}` : "No folder selected" };
  }
  const files = filterBatchVideoFiles(document.getElementById("batchVideoFiles")?.files);
  return {
    mode: "files",
    count: files.length,
    label: files.length ? `${files.length} files selected` : "No files selected",
    files,
  };
}

function getBatchVideoCount() {
  const meta = getBatchSourceMeta();
  if (meta.mode === "server") return null;
  return meta.count ?? 0;
}

function validateBatchStep(step) {
  if (step === 1) {
    const meta = getBatchSourceMeta();
    if (meta.mode === "server") return true;
    if ((meta.count || 0) > 0) return true;
    toast("Choose a folder, select files, or enter a server path", "error");
    return false;
  }
  if (step === 2) {
    const ppd = parseInt(document.getElementById("batchPostsPerDay").value, 10);
    if (!ppd || ppd < 1) {
      toast("Posts per day must be at least 1", "error");
      return false;
    }
    if (!document.getElementById("batchStartDate").value) {
      toast("Choose a start date", "error");
      return false;
    }
    return true;
  }
  return true;
}

function setBatchStep(step) {
  batchCurrentStep = Math.max(1, Math.min(BATCH_STEP_COUNT, step));
  document.querySelectorAll("[data-batch-panel]").forEach((panel) => {
    const n = parseInt(panel.dataset.batchPanel, 10);
    panel.hidden = n !== batchCurrentStep;
    panel.classList.toggle("active", n === batchCurrentStep);
  });
  document.querySelectorAll("[data-batch-step]").forEach((btn) => {
    const n = parseInt(btn.dataset.batchStep, 10);
    btn.classList.toggle("active", n === batchCurrentStep);
    btn.classList.toggle("done", n < batchCurrentStep);
  });
  document.getElementById("btnBatchBack").hidden = batchCurrentStep <= 1;
  document.getElementById("btnBatchNext").hidden = batchCurrentStep >= BATCH_STEP_COUNT;
  document.getElementById("btnBatchScheduleSubmit").hidden = batchCurrentStep !== BATCH_STEP_COUNT;
  if (batchCurrentStep === 2) updateBatchStep2Preview();
  if (batchCurrentStep === 4) updateBatchReviewSummary();
  if (typeof lucide !== "undefined") lucide.createIcons();
}

function updateBatchStep2Preview() {
  const el = document.getElementById("batchStep2Preview");
  if (!el) return;
  const count = getBatchVideoCount();
  const ppd = parseInt(document.getElementById("batchPostsPerDay").value, 10) || 3;
  const slotLines = document.getElementById("batchTimeSlots").value.trim().split("\n").filter(Boolean).length;
  const activeSlots = slotLines || 3;
  if (count === null) {
    el.innerHTML =
      '<p class="form-hint">Server path mode — video count detected when you schedule.</p>';
    return;
  }
  if (!count) {
    el.innerHTML = '<p class="form-hint">Complete Step 1 to estimate duration.</p>';
    return;
  }
  const days = Math.ceil(count / ppd);
  el.innerHTML = `<p class="form-hint"><strong>~${days} days</strong> to post ${count} videos at ${ppd}/day across ${activeSlots} time slot(s).</p>`;
}

function updateBatchReviewSummary() {
  const meta = getBatchSourceMeta();
  const ppd = document.getElementById("batchPostsPerDay").value;
  const start = document.getElementById("batchStartDate").value;
  const count = getBatchVideoCount();
  const days = count ? Math.ceil(count / (parseInt(ppd, 10) || 3)) : "—";

  document.getElementById("batchReviewSummary").innerHTML = `
    <div class="batch-review-stat"><strong>${count ?? "?"}</strong><span>Videos</span></div>
    <div class="batch-review-stat"><strong>${ppd}</strong><span>Per day</span></div>
    <div class="batch-review-stat"><strong>${days}</strong><span>Est. days</span></div>
    <div class="batch-review-stat"><strong>${escapeHtml(start || "—")}</strong><span>Start</span></div>
    <div class="batch-review-stat" style="grid-column:1/-1"><strong style="font-size:0.85rem;font-weight:500">${escapeHtml(meta.label)}</strong><span>Source</span></div>
  `;

  const filesEl = document.getElementById("batchReviewFiles");
  if (meta.mode === "files" && meta.files?.length) {
    renderBatchFilePreview(meta.files, "batchReviewFiles");
  } else if (batchFolderVideoFiles.length) {
    renderBatchFilePreview(batchFolderVideoFiles, "batchReviewFiles");
  } else if (meta.mode === "server") {
    filesEl.innerHTML = `<p class="form-hint">Videos from server folder:<br><code>${escapeHtml(meta.label)}</code></p>`;
  } else {
    filesEl.innerHTML = '<p class="form-hint">No file list available.</p>';
  }
}

function onBatchFolderPicked(fileList) {
  batchFolderVideoFiles = filterBatchVideoFiles(fileList);
  const label = document.getElementById("batchFolderLabel");
  if (!batchFolderVideoFiles.length) {
    if (label) label.textContent = "No videos in folder";
    renderBatchFilePreview([], "batchStep1Preview");
    updateBatchStep2Preview();
    return;
  }
  const root = (batchFolderVideoFiles[0].webkitRelativePath || "").split("/")[0] || "folder";
  if (label) {
    label.textContent = `${batchFolderVideoFiles.length} videos — ${root}`;
  }
  const header = `<p class="form-hint"><strong>${escapeHtml(root)}</strong> — ${batchFolderVideoFiles.length} videos</p>`;
  renderBatchFilePreview(batchFolderVideoFiles, "batchStep1Preview", header);
  updateBatchStep2Preview();
  if (batchCurrentStep === 4) updateBatchReviewSummary();
}

function onBatchFilesPicked(fileList) {
  const files = filterBatchVideoFiles(fileList);
  const label = document.getElementById("batchFilesLabel");
  if (label) {
    label.textContent = files.length
      ? `${files.length} file${files.length === 1 ? "" : "s"} selected`
      : "No video files selected";
  }
  renderBatchFilePreview(files, "batchStep1Preview");
  updateBatchStep2Preview();
  if (batchCurrentStep === 4) updateBatchReviewSummary();
}

function openBatchSchedule(accountId) {
  const acc = accountsCache.find((a) => a.id === accountId);
  document.getElementById("batchScheduleAccountId").value = String(accountId);
  document.getElementById("batchScheduleTitle").textContent =
    `Batch schedule — @${acc?.username || accountId}`;
  document.querySelector('input[name="batchSource"][value="folder"]').checked = true;
  batchFolderVideoFiles = [];
  document.getElementById("batchFolderPath").value = "";
  const folderPicker = document.getElementById("batchFolderPicker");
  if (folderPicker) folderPicker.value = "";
  document.getElementById("batchFolderLabel").textContent = "No folder selected";
  document.getElementById("batchVideoFiles").value = "";
  document.getElementById("batchFilesLabel").textContent = "No files selected";
  document.getElementById("batchPostsPerDay").value = "3";
  document.getElementById("batchTimeSlots").value = "08:00-11:00\n14:00-17:00\n19:00-22:00";
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  document.getElementById("batchStartDate").value = tomorrow.toISOString().slice(0, 10);
  document.getElementById("batchCaptionTemplate").value = "Video {n}";
  document.getElementById("batchHashtags").value = "fyp foryou viral";
  document.getElementById("batchAffiliateLink").value = "";
  document.getElementById("batchStep1Preview").innerHTML =
    '<p class="form-hint">Select a folder or files to continue.</p>';
  document.getElementById("batchStep2Preview").innerHTML = "";
  document.getElementById("batchReviewSummary").innerHTML = "";
  document.getElementById("batchReviewFiles").innerHTML = "";
  document.getElementById("batchQueuePreview").innerHTML =
    '<p class="form-hint">Pending scheduled posts for this account.</p>';
  updateBatchSourceRows();
  setBatchStep(1);
  openModal("modalBatchSchedule");
  if (typeof lucide !== "undefined") lucide.createIcons();
}

async function refreshBatchQueuePreview() {
  const accountId = parseInt(document.getElementById("batchScheduleAccountId").value, 10);
  const el = document.getElementById("batchQueuePreview");
  try {
    const r = await API.get(`/api/actions/post/${accountId}/batch-queue`);
    if (!r.pending?.length) {
      el.innerHTML = '<p class="form-hint">No pending scheduled posts for this account.</p>';
      return;
    }
    el.innerHTML = `<table class="data-table batch-queue-table"><thead><tr><th>#</th><th>File</th><th>Scheduled</th><th>Caption</th></tr></thead><tbody>${r.pending
      .map(
        (p, i) =>
          `<tr><td>${i + 1}</td><td>${escapeHtml(p.file)}</td><td>${formatDate(p.scheduled_at)}</td><td>${escapeHtml((p.caption || "").slice(0, 40))}</td></tr>`
      )
      .join("")}</tbody></table>`;
  } catch (e) {
    el.innerHTML = `<p class="form-hint" style="color:var(--danger)">${escapeHtml(e.message)}</p>`;
  }
}

async function submitBatchSchedule() {
  const accountId = parseInt(document.getElementById("batchScheduleAccountId").value, 10);
  const useFolder = document.querySelector('input[name="batchSource"]:checked')?.value === "folder";
  for (let s = 1; s <= 3; s++) {
    if (!validateBatchStep(s)) {
      setBatchStep(s);
      return;
    }
  }
  const btn = document.getElementById("btnBatchScheduleSubmit");
  setButtonLoading(btn, true);
  const toastEl = toast("Building schedule…", "loading");
  try {
    const fd = new FormData();
    if (useFolder) {
      const serverPath = document.getElementById("batchFolderPath").value.trim();
      if (serverPath) {
        fd.append("folder_path", serverPath);
      } else if (batchFolderVideoFiles.length) {
        batchFolderVideoFiles.forEach((f) => fd.append("files", f));
      } else {
        throw new Error("Choose a folder or enter a server path");
      }
    } else {
      const files = filterBatchVideoFiles(document.getElementById("batchVideoFiles").files);
      if (!files.length) {
        throw new Error("Choose at least one video file");
      }
      files.forEach((f) => fd.append("files", f));
    }
    fd.append("posts_per_day", document.getElementById("batchPostsPerDay").value || "3");
    fd.append("time_slots", document.getElementById("batchTimeSlots").value.trim());
    fd.append("start_date", document.getElementById("batchStartDate").value);
    fd.append("caption_template", document.getElementById("batchCaptionTemplate").value);
    fd.append("hashtags", document.getElementById("batchHashtags").value);
    fd.append("affiliate_link", document.getElementById("batchAffiliateLink").value);

    const r = await API.postFormData(`/api/actions/post/${accountId}/batch-schedule`, fd);
    clearToast(toastEl);
    closeModal("modalBatchSchedule");
    toast(
      `Queued ${r.count} videos (${r.posts_per_day}/day). First: ${formatDate(r.first_scheduled_at)} — Last: ${formatDate(r.last_scheduled_at)} (~${r.estimated_days} days)`,
      "success"
    );
    refreshPosts();
  } catch (e) {
    clearToast(toastEl);
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function generatePostPreview() {
  const accountId = parseInt(document.getElementById("postComposerAccountId").value, 10);
  const body = {
    caption: document.getElementById("postCaption").value,
    hashtags: document.getElementById("postHashtags").value,
    affiliate_link: document.getElementById("postAffiliateLink").value,
    template_name: document.getElementById("postTemplate").value,
  };
  const btn = document.getElementById("btnPostGenerate");
  setButtonLoading(btn, true);
  const toastEl = toast("Generating preview…", "loading");
  try {
    const r = await API.postJson(`/api/actions/preview/${accountId}`, body);
    clearToast(toastEl);
    document.getElementById("postImagesDir").value = r.images_dir || "";
    if (r.caption_preview) document.getElementById("postCaption").value = r.caption_preview.split("\n\n")[0];
    const strip = document.getElementById("postPreviewStrip");
    strip.innerHTML = (r.images || [])
      .map((url) => `<img src="${url}" alt="slide" loading="lazy">`)
      .join("") || '<p class="form-hint">No images generated</p>';
    toast("Preview ready");
  } catch (e) {
    clearToast(toastEl);
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function submitPostComposer() {
  const accountId = parseInt(document.getElementById("postComposerAccountId").value, 10);
  const isVideo = document.querySelector('input[name="postType"]:checked')?.value === "video";
  const btn = document.getElementById("btnPostSubmit");
  const caption = document.getElementById("postCaption").value;
  const hashtags = document.getElementById("postHashtags").value;
  const affiliateLink = document.getElementById("postAffiliateLink").value;

  if (isVideo) {
    const videoFile = document.getElementById("postVideoFile")?.files?.[0];
    if (!videoFile) {
      toast("Choose a video file", "error");
      return;
    }
    setButtonLoading(btn, true);
    closeModal("modalPostComposer");
    const toastEl = toast("Uploading video…", "loading");
    try {
      const fd = new FormData();
      fd.append("file", videoFile);
      fd.append("caption", caption);
      fd.append("hashtags", hashtags);
      fd.append("affiliate_link", affiliateLink);
      const r = await API.postFormData(`/api/actions/upload/video/${accountId}`, fd);
      clearToast(toastEl);
      toast(r.success ? "Video posted" : (r.result?.error || "Upload failed"), r.success ? "success" : "error");
      refreshPosts();
    } catch (e) {
      clearToast(toastEl);
      toast(e.message, "error");
    } finally {
      setButtonLoading(btn, false);
    }
    return;
  }

  const imageFiles = document.getElementById("postSlideshowFiles")?.files;
  if (imageFiles?.length) {
    setButtonLoading(btn, true);
    closeModal("modalPostComposer");
    const toastEl = toast("Uploading slideshow…", "loading");
    try {
      const fd = new FormData();
      Array.from(imageFiles).forEach((f) => fd.append("files", f));
      fd.append("caption", caption);
      fd.append("hashtags", hashtags);
      fd.append("affiliate_link", affiliateLink);
      const r = await API.postFormData(`/api/actions/post/${accountId}/slideshow-files`, fd);
      clearToast(toastEl);
      if (r.session_id) {
        showLiveLogPanel("Post upload", accountId);
        connectLiveWs(r.session_id, { type: "post", accountId });
        toast("Post started");
      } else {
        toast(r.message || "Done");
        refreshPosts();
      }
    } catch (e) {
      clearToast(toastEl);
      toast(e.message, "error");
    } finally {
      setButtonLoading(btn, false);
    }
    return;
  }

  setButtonLoading(btn, true);
  closeModal("modalPostComposer");
  const body = {
    caption,
    hashtags,
    affiliate_link: affiliateLink,
    template_name: document.getElementById("postTemplate").value,
    images_dir: document.getElementById("postImagesDir").value,
  };
  const toastEl = toast("Posting slideshow…", "loading");
  try {
    const r = await API.postJson(`/api/actions/post/${accountId}`, body);
    clearToast(toastEl);
    if (r.session_id) {
      showLiveLogPanel("Post upload", accountId);
      connectLiveWs(r.session_id, { type: "post", accountId });
      toast("Post started");
    } else {
      toast(r.message || "Done");
      refreshPosts();
    }
  } catch (e) {
    clearToast(toastEl);
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

const LOG_LEVEL_ICON = {
  SUCCESS: "✅",
  INFO: "💚",
  WARNING: "⚠️",
  ERROR: "🔴",
};

function formatLogTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
    return d.toLocaleTimeString(undefined, { hour12: false });
  } catch {
    return iso.slice(11, 19) || iso;
  }
}

function renderAccountLogsList() {
  const body = document.getElementById("accountLogsBody");
  const foot = document.getElementById("accountLogsFoot");
  if (!body) return;
  let rows = accountLogsEntries;
  if (accountLogsFilter === "error") {
    rows = rows.filter((e) => e.level === "ERROR" || e.log_type === "error");
  } else if (accountLogsFilter) {
    rows = rows.filter((e) => e.log_type === accountLogsFilter);
  }
  if (!rows.length) {
    body.innerHTML = '<div class="empty-state">No logs yet</div>';
  } else {
    body.innerHTML = rows
      .map((e) => {
        const icon = LOG_LEVEL_ICON[e.level] || "·";
        const lvl = (e.level || "INFO").toUpperCase();
        return `<div class="log-entry log-${lvl}">
          <span class="log-time">${formatLogTime(e.created_at)}</span>
          <span class="log-type">[${escapeHtml(e.log_type || "system")}]</span>
          ${icon} ${escapeHtml(e.message || "")}
        </div>`;
      })
      .join("");
  }
  if (foot) foot.textContent = `${rows.length} log(s) shown`;
}

function disconnectAccountLogsWs() {
  if (accountLogsWs) {
    accountLogsWs.close();
    accountLogsWs = null;
  }
}

function connectAccountLogsWs(accountId) {
  disconnectAccountLogsWs();
  if (!document.getElementById("logAutoRefresh")?.checked) return;
  const url = wsUrl(`/api/logs/${accountId}`);
  accountLogsWs = new WebSocket(url);
  accountLogsWs.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "log" && msg.data) {
        accountLogsEntries.unshift(msg.data);
        if (accountLogsEntries.length > 200) accountLogsEntries.pop();
        renderAccountLogsList();
      }
    } catch {
      /* ignore */
    }
  };
  accountLogsWs.onclose = () => {
    if (
      accountLogsAccountId === accountId &&
      document.getElementById("modalAccountLogs")?.classList.contains("open") &&
      document.getElementById("logAutoRefresh")?.checked
    ) {
      setTimeout(() => {
        if (accountLogsAccountId === accountId) connectAccountLogsWs(accountId);
      }, 3000);
    }
  };
}

async function loadAccountLogs(accountId) {
  const q = new URLSearchParams({ limit: "50" });
  if (accountLogsFilter && accountLogsFilter !== "error") q.set("type", accountLogsFilter);
  if (accountLogsFilter === "error") q.set("level", "ERROR");
  const r = await API.get(`/api/accounts/${accountId}/logs?${q}`);
  accountLogsEntries = r.logs || [];
  renderAccountLogsList();
}

async function openLogPanel(accountId) {
  const acc = accountsCache.find((a) => a.id === accountId);
  const title = document.getElementById("accountLogsTitle");
  if (title) title.textContent = acc ? `Logs — @${acc.username}` : `Logs — #${accountId}`;
  accountLogsAccountId = accountId;
  accountLogsFilter = "";
  document.querySelectorAll("#logFilters .log-filter").forEach((b) => {
    b.classList.toggle("active", b.dataset.logFilter === "");
  });
  openModal("modalAccountLogs");
  const body = document.getElementById("accountLogsBody");
  if (body) body.innerHTML = '<div class="empty-state">Loading logs…</div>';
  try {
    await loadAccountLogs(accountId);
    connectAccountLogsWs(accountId);
  } catch (e) {
    if (body) body.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    toast(e.message, "error");
  }
}

function closeAccountLogsPanel() {
  disconnectAccountLogsWs();
  accountLogsAccountId = null;
  accountLogsEntries = [];
}

async function clearAccountLogs() {
  if (!accountLogsAccountId) return;
  if (!confirm("Clear all logs for this account?")) return;
  try {
    await API.delete(`/api/accounts/${accountLogsAccountId}/logs`);
    accountLogsEntries = [];
    renderAccountLogsList();
    toast("Logs cleared");
  } catch (e) {
    toast(e.message, "error");
  }
}

async function checkSingleProxy(proxyId, btn) {
  setButtonLoading(btn, true);
  try {
    const r = await API.post(`/api/proxies/${proxyId}/check`);
    toast(r.alive ? `Proxy #${proxyId} is live` : `Proxy #${proxyId} failed`, r.alive ? "success" : "error");
    refreshProxies();
  } catch (e) {
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function syncProxiesFromCsv() {
  const btn = document.getElementById("btnProxySync");
  setButtonLoading(btn, true);
  const toastEl = toast("Syncing proxies from CSV…", "loading");
  try {
    const r = await API.post("/api/proxies/sync");
    clearToast(toastEl);
    const s = r.stats || {};
    toast(
      `Sync done: +${s.inserted ?? 0} · updated ${s.updated ?? 0} · removed ${s.deleted ?? 0} · migrated ${s.migrated_accounts ?? 0}`
    );
    refreshProxies();
    refreshAccounts();
    refreshStats();
  } catch (e) {
    clearToast(toastEl);
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

async function checkAllProxies(ev) {
  const btn = ev?.currentTarget || document.getElementById("btnProxyCheck");
  setButtonLoading(btn, true);
  const toastEl = toast("Checking proxies\u2026", "loading");
  try {
    const r = await API.post("/api/proxies/check-all");
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

// ---- Affiliate v2.0 ----

async function refreshAffiliate() {
  const statusWrap = document.getElementById("affiliateStatusPanel");
  const tableWrap = document.getElementById("affiliateProductsTable");
  if (!statusWrap) return;

  try {
    const [st, trending] = await Promise.all([
      API.get("/api/affiliate/status"),
      API.get("/api/affiliate/trending"),
    ]);
    const s = st.status || {};
    statusWrap.innerHTML = `
      <div class="settings-card">
        <h4>Pipeline tools</h4>
        <div class="profile-stats">
          <span>ffmpeg: ${s.ffmpeg ? "yes" : "no"}</span>
          <span>yt-dlp: ${s.yt_dlp ? "yes" : "no"}</span>
          <span>Min commission: ${s.commission_min_pct || 10}%</span>
          <span>Cached SP: ${s.trending_count || 0}</span>
        </div>
        <p class="form-hint" style="margin-top:0.5rem">Real accounts (IDs in settings) use video pipeline; Farm accounts use slideshow.</p>
      </div>`;

    const products = trending.products || [];
    const badge = document.getElementById("navBadgeAffiliate");
    if (badge) badge.textContent = String(products.length);

    if (!tableWrap) return;
    if (!products.length) {
      tableWrap.innerHTML = `<div class="empty-state">No products cached. Click <strong>Scan SP</strong>.</div>`;
      return;
    }

    tableWrap.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>SP</th><th>Name</th><th>Price</th><th>Commission</th><th>Link</th>
          </tr>
        </thead>
        <tbody>
          ${products.map((p) => `
            <tr>
              <td class="cell-mono">${escapeHtml(p.sp_id || "—")}</td>
              <td>${escapeHtml(p.name || "—")}</td>
              <td class="cell-mono">${p.price ?? "—"}</td>
              <td class="cell-mono">${p.commission_pct ?? 0}%</td>
              <td>${p.affiliate_link ? `<a href="${escapeHtml(p.affiliate_link)}" target="_blank" rel="noopener">open</a>` : "—"}</td>
            </tr>`).join("")}
        </tbody>
      </table>`;
  } catch (e) {
    statusWrap.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
  }
}

async function scanAffiliateProducts() {
  const btn = document.getElementById("btnAffiliateScan");
  setButtonLoading(btn, true);
  try {
    toast("Scanning trending products…");
    const r = await API.post("/api/affiliate/scan?limit=20");
    toast(`Found ${r.count || 0} products`);
    refreshAffiliate();
  } catch (e) {
    toast(e.message, "error");
  } finally {
    setButtonLoading(btn, false);
  }
}

// ---- Settings ----

async function refreshSettings() {
  const wrap = document.getElementById("settingsPanel");
  if (!wrap) return;
  try {
    const r = await API.get("/api/profile/status");
    const s = r.status || {};
    const ready = s.ready;
    wrap.innerHTML = `
      <div class="settings-card">
        <h4><i data-lucide="scan" style="width:16px;height:16px"></i> Profile Scanner</h4>
        <div class="status-line">
          Status: ${ready ? '<span class="status-dot-sm ok"></span>Ready' : '<span class="status-dot-sm err"></span>Not ready'}
          &middot; Browser: <code>${escapeHtml(s.engine || "Chromium")}</code>
          ${s.headless === false ? " &middot; headed" : " &middot; headless"}
        </div>
        <div class="status-line">
          ${escapeHtml(s.message || "Uses Playwright to read public TikTok profiles — no ms_token required.")}
        </div>
        <div class="field-row" style="margin-top:0.75rem">
          <button class="btn btn-sm btn-primary" type="button" id="btnTestProfileScan">
            <i data-lucide="play"></i> Test Scan @tiktok
          </button>
        </div>
        <div id="settingsResult" style="margin-top:0.5rem"></div>
      </div>
      <div class="settings-card">
        <h4><i data-lucide="info" style="width:16px;height:16px"></i> How it works</h4>
        <div style="font-size:0.8rem;color:var(--text-muted);line-height:1.6">
          Opens <code>https://www.tiktok.com/@username</code> in Chromium and reads followers, following, and likes from the page.
          Assign a proxy on the account row if your server IP is blocked.
        </div>
      </div>`;

    if (typeof lucide !== "undefined") lucide.createIcons();
    document.getElementById("btnTestProfileScan")?.addEventListener("click", testProfileScan);
  } catch (e) {
    wrap.innerHTML = `<div class="empty-state">Error: ${escapeHtml(e.message)}</div>`;
  }
}

async function testProfileScan() {
  const result = document.getElementById("settingsResult");
  result.innerHTML = '<span style="color:var(--text-muted)">Scanning @tiktok…</span>';
  const btn = document.getElementById("btnTestProfileScan");
  setButtonLoading(btn, true);
  try {
    const r = await API.post("/api/profile/scan/tiktok");
    if (r.success) {
      result.innerHTML = `<span style="color:var(--success)">OK — @${escapeHtml(r.username)}: ${formatNum(r.followers)} followers, ${formatNum(r.likes)} likes</span>`;
      toast(`Scanner OK — ${formatNum(r.followers)} followers`);
    } else {
      result.innerHTML = `<span style="color:var(--warning)">${escapeHtml(profileErrorToast(r, "tiktok"))}</span>`;
      toast(profileErrorToast(r, "tiktok"), "error");
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
      if (id === "posts") refreshPosts();
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

  const sections = ["overview", "accounts", "proxies", "alerts", "affiliate", "settings"];
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
  if (id === "modalAccountLogs") closeAccountLogsPanel();
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
  const parseErr = (result.parse_errors || []).length;
  el.innerHTML = `
    <strong>Import complete</strong><br>
    Imported: ${result.imported ?? 0} · Skipped: ${result.skipped ?? 0} · Failed: ${result.failed ?? 0}
    ${result.with_cookies != null ? `<br>With cookies: ${result.with_cookies}` : ""}
    ${result.without_cookies != null ? `<br>Missing cookies: ${result.without_cookies}` : ""}
    ${result.total_rows != null ? `<br>Rows in file: ${result.total_rows}` : ""}
    ${parseErr ? `<br><span style="color:var(--warning)">${parseErr} parse error(s)</span>` : ""}
    ${errCount ? `<br><span style="color:var(--warning)">${errCount} row error(s)</span>` : ""}`;
}

async function submitAccountModal() {
  const btn = document.getElementById("btnSubmitAccount");
  if (btn) btn.disabled = true;
  try {
    if (accountModalTab === "account-seller") {
      await submitSellerImport("accountImportResult");
      return;
    }
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
      const payload = {
        username,
        proxy_id: parseInt(document.getElementById("accProxyId")?.value || "0", 10),
        password: document.getElementById("accPassword")?.value || "",
        notes: document.getElementById("accNotes")?.value || "",
        status: document.getElementById("accStatus")?.value || "pending",
      };
      const cookies = document.getElementById("accCookies")?.value?.trim();
      if (cookies) payload.cookie_data = cookies;
      await API.postJson("/api/accounts", payload);
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
  document.addEventListener("click", () => {
    document.querySelectorAll(".actions-dropdown.open").forEach((d) => d.classList.remove("open"));
  });

  document.getElementById("liveLogClose")?.addEventListener("click", hideLiveLogPanel);
  document.getElementById("btnPostGenerate")?.addEventListener("click", generatePostPreview);
  document.getElementById("btnPostSubmit")?.addEventListener("click", submitPostComposer);
  document.getElementById("btnBatchScheduleSubmit")?.addEventListener("click", submitBatchSchedule);
  document.getElementById("btnBatchQueueRefresh")?.addEventListener("click", refreshBatchQueuePreview);
  document.getElementById("btnBatchBrowseFolder")?.addEventListener("click", () => {
    document.getElementById("batchFolderPicker")?.click();
  });
  document.getElementById("batchFolderPicker")?.addEventListener("change", (e) => {
    onBatchFolderPicked(e.target.files);
  });
  document.getElementById("batchVideoFiles")?.addEventListener("change", (e) => {
    onBatchFilesPicked(e.target.files);
  });
  document.getElementById("batchPostsPerDay")?.addEventListener("input", updateBatchStep2Preview);
  document.getElementById("batchTimeSlots")?.addEventListener("input", updateBatchStep2Preview);
  document.getElementById("batchFolderPath")?.addEventListener("input", () => {
    updateBatchStep2Preview();
    if (batchCurrentStep === 4) updateBatchReviewSummary();
  });
  document.querySelectorAll('input[name="batchSource"]').forEach((el) => {
    el.addEventListener("change", updateBatchSourceRows);
  });
  document.querySelectorAll("[data-batch-step]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = parseInt(btn.dataset.batchStep, 10);
      if (target < batchCurrentStep) {
        setBatchStep(target);
        return;
      }
      for (let s = batchCurrentStep; s < target; s++) {
        if (!validateBatchStep(s)) return;
      }
      setBatchStep(target);
    });
  });
  document.getElementById("btnBatchBack")?.addEventListener("click", () => {
    if (batchCurrentStep > 1) setBatchStep(batchCurrentStep - 1);
  });
  document.getElementById("btnBatchNext")?.addEventListener("click", () => {
    if (!validateBatchStep(batchCurrentStep)) return;
    if (batchCurrentStep < BATCH_STEP_COUNT) setBatchStep(batchCurrentStep + 1);
  });
  document.getElementById("btnRefreshPosts")?.addEventListener("click", refreshPosts);
  document.getElementById("postsFilterAccount")?.addEventListener("change", refreshPosts);
  document.querySelectorAll('input[name="postType"]').forEach((el) => {
    el.addEventListener("change", updatePostTypeRows);
  });
  document.getElementById("postSlideshowFiles")?.addEventListener("change", (e) => {
    previewSlideshowFiles(e.target.files);
    document.getElementById("postImagesDir").value = "";
  });
  document.getElementById("postVideoFile")?.addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    const strip = document.getElementById("postPreviewStrip");
    if (!file) {
      strip.innerHTML = '<p class="form-hint">Choose a video file above</p>';
      return;
    }
    strip.innerHTML = `<p class="form-hint post-video-selected"><strong>${escapeHtml(file.name)}</strong> (${(file.size / 1024 / 1024).toFixed(1)} MB)</p>`;
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
  document.getElementById("btnProxySync")?.addEventListener("click", syncProxiesFromCsv);
  document.getElementById("btnClearAccountLogs")?.addEventListener("click", clearAccountLogs);
  document.getElementById("logAutoRefresh")?.addEventListener("change", (e) => {
    if (e.target.checked && accountLogsAccountId) connectAccountLogsWs(accountLogsAccountId);
    else disconnectAccountLogsWs();
  });
  document.querySelectorAll("#logFilters .log-filter").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document.querySelectorAll("#logFilters .log-filter").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      accountLogsFilter = btn.dataset.logFilter || "";
      if (accountLogsAccountId) {
        try {
          await loadAccountLogs(accountLogsAccountId);
        } catch (err) {
          toast(err.message, "error");
        }
      }
    });
  });
  document.getElementById("btnReschedule")?.addEventListener("click", rescheduleJobs);
  document.getElementById("btnAffiliateScan")?.addEventListener("click", scanAffiliateProducts);
  document.getElementById("btnAffiliateRefresh")?.addEventListener("click", refreshAffiliate);
  document.getElementById("btnSellerImport")?.addEventListener("click", () =>
    submitSellerImport("sellerImportResult")
  );

  refreshAll();
  refreshTimer = setInterval(refreshAll, 60000);
}

document.addEventListener("DOMContentLoaded", init);
