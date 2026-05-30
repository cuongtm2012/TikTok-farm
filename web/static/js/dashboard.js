/**
 * TikTok Farm Dashboard
 * Data layer + UI updates (ui-ux-pro-max: monitoring dashboard patterns)
 */

const API = {
  async get(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(`${path} → ${res.status}`);
    return res.json();
  },
  async post(path) {
    const res = await fetch(path, { method: "POST" });
    if (!res.ok) throw new Error(`${path} → ${res.status}`);
    return res.json();
  },
};

let statusChart = null;
let performanceChart = null;
let refreshTimer = null;

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
  setTimeout(() => el.remove(), 4000);
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
  if (pill) {
    pill.className = "status-pill " + (alerts > 0 ? "warn" : flagged > 0 ? "err" : "ok");
    pill.innerHTML =
      `<span class="status-dot"></span>` +
      (alerts > 0 ? `${alerts} alerts` : flagged > 0 ? `${flagged} flagged` : "All systems go");
  }

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

async function refreshAccounts() {
  const wrap = document.getElementById("accountsTable");
  const data = await API.get("/api/accounts");
  if (!data.success) {
    wrap.innerHTML = `<div class="empty-state">Failed to load accounts</div>`;
    return;
  }

  const accounts = data.accounts || [];
  if (!accounts.length) {
    wrap.innerHTML = `
      <div class="empty-state">
        <p>No accounts yet</p>
        <p style="margin-top:0.5rem;font-size:0.8rem">Add via config/accounts.yaml or API</p>
      </div>`;
    return;
  }

  wrap.innerHTML = `
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
        ${accounts
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
                <button class="btn btn-sm" type="button" data-action="farm" data-id="${a.id}" title="Farm session">Farm</button>
                <button class="btn btn-sm" type="button" data-action="post" data-id="${a.id}" title="Upload post">Post</button>
                <button class="btn btn-sm" type="button" data-action="check" data-id="${a.id}" title="Health check">Check</button>
              </div>
            </td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`;

  wrap.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => runAction(btn.dataset.action, parseInt(btn.dataset.id, 10)));
  });
}

async function refreshProxies() {
  const wrap = document.getElementById("proxiesTable");
  if (!wrap) return;
  const data = await API.get("/api/proxies");
  if (!data.success) {
    wrap.innerHTML = `<div class="empty-state">Failed to load proxies</div>`;
    return;
  }

  const proxies = data.proxies || [];
  if (!proxies.length) {
    wrap.innerHTML = `<div class="empty-state"><p>No proxies in config/proxies.csv</p></div>`;
    return;
  }

  wrap.innerHTML = `
    <table>
      <thead>
        <tr><th>ID</th><th>Endpoint</th><th>Protocol</th><th>Status</th><th>Fails</th></tr>
      </thead>
      <tbody>
        ${proxies
          .map(
            (p) => `
          <tr>
            <td style="font-family:var(--font-mono)">${p.id}</td>
            <td style="font-family:var(--font-mono);font-size:0.8rem">${escapeHtml(p.ip)}:${p.port}</td>
            <td>${escapeHtml(p.protocol)}</td>
            <td><span class="badge ${p.status === "active" ? "badge-active" : "badge-banned"}">${p.status}</span></td>
            <td style="font-family:var(--font-mono)">${p.fail_count || 0}</td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

async function refreshAlerts() {
  const wrap = document.getElementById("alertsList");
  const data = await API.get("/api/alerts?resolved=0");
  if (!data.success) {
    wrap.innerHTML = `<div class="empty-state">Failed to load alerts</div>`;
    return;
  }

  const alerts = data.alerts || [];
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

async function runAction(action, accountId) {
  const paths = {
    farm: `/api/actions/farm/${accountId}`,
    post: `/api/actions/post/${accountId}`,
    check: `/api/actions/check/${accountId}`,
  };
  try {
    toast(`Starting ${action} for account ${accountId}…`);
    const result = await API.post(paths[action]);
    toast(result.message || `${action} completed`, "success");
    setTimeout(refreshAll, 2000);
  } catch (e) {
    toast(e.message, "error");
  }
}

async function checkAllProxies() {
  try {
    toast("Checking proxies…");
    const r = await API.post("/api/proxies/check");
    const res = r.results || {};
    toast(`Proxies: ${res.alive ?? 0} alive, ${res.dead ?? 0} dead`);
    refreshProxies();
    refreshStats();
  } catch (e) {
    toast(e.message, "error");
  }
}

async function rescheduleJobs() {
  try {
    const r = await API.post("/api/scheduler/reschedule");
    toast(r.message || "Jobs rescheduled");
  } catch (e) {
    toast(e.message, "error");
  }
}

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str ?? "";
  return d.innerHTML;
}

function initNav() {
  document.querySelectorAll(".nav a[data-section]").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const id = link.getAttribute("data-section");
      document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
      document.querySelectorAll(".nav a").forEach((a) => a.classList.remove("active"));
      link.classList.add("active");
      document.getElementById("sidebar")?.classList.remove("open");
    });
  });

  const toggle = document.getElementById("menuToggle");
  const sidebar = document.getElementById("sidebar");
  toggle?.addEventListener("click", () => sidebar?.classList.toggle("open"));
}

function init() {
  initNav();
  document.getElementById("btnRefresh")?.addEventListener("click", refreshAll);
  document.getElementById("btnProxyCheck")?.addEventListener("click", checkAllProxies);
  document.getElementById("btnReschedule")?.addEventListener("click", rescheduleJobs);

  refreshAll();
  refreshTimer = setInterval(refreshAll, 60000);
}

document.addEventListener("DOMContentLoaded", init);
