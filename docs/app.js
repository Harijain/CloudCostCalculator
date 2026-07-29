// ---------------------------
// Storage keys
// ---------------------------
const LS = {
  theme: "ccc.theme",
  budget: "ccc.budgetLimit",
  mode: "ccc.mode", // "live" | "sample"
};

const thresholds = [50, 100, 200];

// ---------------------------
// Helpers
// ---------------------------
const $ = (id) => document.getElementById(id);

const setText = (id, text) => {
  const el = $(id);
  if (el) el.textContent = text;
};

const formatCurrency = (value) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(value);

const formatPercent = (value) => `${value.toFixed(1)}%`;

const cleanBase = (v) => String(v || "").trim().replace(/\/$/, "");

// ---------------------------
// Theme
// ---------------------------
const applyTheme = (mode) => {
  localStorage.setItem(LS.theme, mode);
  const root = document.documentElement;

  if (mode === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", mode);

  ["theme-system", "theme-light", "theme-dark"].forEach((id) => $(id)?.classList.remove("active"));
  if (mode === "system") $("theme-system")?.classList.add("active");
  if (mode === "light") $("theme-light")?.classList.add("active");
  if (mode === "dark") $("theme-dark")?.classList.add("active");
};

const initTheme = () => {
  const mode = localStorage.getItem(LS.theme) || "system";
  applyTheme(mode);
};

// ---------------------------
// Mode (Live / Sample)
// ---------------------------
const setMode = (mode) => {
  localStorage.setItem(LS.mode, mode);

  $("btn-live")?.classList.toggle("active", mode === "live");
  $("btn-sample")?.classList.toggle("active", mode === "sample");

  const chip = $("mode-chip");
  if (chip) {
    chip.classList.remove("live", "sample");
    chip.classList.add(mode === "live" ? "live" : "sample");
    chip.textContent = mode === "live" ? "LIVE" : "SAMPLE";
  }
};

const getMode = () => localStorage.getItem(LS.mode) || "live";

// ---------------------------
// Budget (UI-only)
// ---------------------------
const getBudgetLimit = () => {
  const raw = localStorage.getItem(LS.budget);
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : 0;
};
const setBudgetLimit = (n) => localStorage.setItem(LS.budget, String(n));

// ---------------------------
// Connection UI
// ---------------------------
const setConnected = (ok, msg = "") => {
  const dot = $("conn-dot");
  const text = $("conn-status");
  if (!dot || !text) return;

  dot.classList.toggle("ok", !!ok);
  text.textContent = ok ? "Connected" : "Not connected";
  if (msg) setText("live-msg", msg);
};

// ---------------------------
// Data loaders
// ---------------------------
let API_BASE = "";

const loadConfig = async () => {
  const res = await fetch("./config.json?t=" + Date.now(), { cache: "no-store" });
  if (!res.ok) throw new Error(`config.json not found (${res.status}). Upload it beside index.html.`);
  const data = await res.json();
  const apiBase = cleanBase(data.apiBaseUrl || data.apiBase || "");
  if (!apiBase) throw new Error("config.json is missing apiBaseUrl.");
  return apiBase;
};

const loadSnapshot = async (apiBase) => {
  const res = await fetch(`${apiBase}/snapshot?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
  return res.json();
};

const loadMapping = async (apiBase) => {
  const res = await fetch(`${apiBase}/mapping?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Mapping error ${res.status}: ${await res.text()}`);
  return res.json();
};

const saveMapping = async (apiBase, token, mapping) => {
  const res = await fetch(`${apiBase}/mapping`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "x-admin-token": token,
    },
    body: JSON.stringify({ mapping }),
  });
  if (!res.ok) throw new Error(`Save failed ${res.status}: ${await res.text()}`);
};

const loadSample = async () => {
  const res = await fetch("./data/sample-costs.json?t=" + Date.now(), { cache: "no-store" });
  if (!res.ok) throw new Error("Unable to load sample data. Ensure /data/sample-costs.json exists.");
  return res.json();
};

// ---------------------------
// Rendering
// ---------------------------
const createAlert = (message, type = "warn") => {
  const item = document.createElement("li");
  item.textContent = message;
  if (type === "ok") item.classList.add("ok");
  return item;
};

// Gauge geometry: semicircle from 180deg (left, 0%) to 0deg (right, 100%+).
// Pivot at (100,110), needle length 70 — matches the arc paths drawn in index.html.
const setGaugeNeedle = (pctForNeedle) => {
  const needle = $("gauge-needle");
  if (!needle) return;
  const clamped = Math.max(0, Math.min(pctForNeedle, 100));
  const angleDeg = 180 - (clamped / 100) * 180;
  const angleRad = (angleDeg * Math.PI) / 180;
  const cx = 100, cy = 110, r = 70;
  const x2 = cx + r * Math.cos(angleRad);
  const y2 = cy - r * Math.sin(angleRad);
  needle.setAttribute("x2", x2.toFixed(1));
  needle.setAttribute("y2", y2.toFixed(1));
};

const renderBudgetCard = (totalSpend) => {
  const limit = getBudgetLimit();
  if (!limit) {
    setText("budget-remaining", "$—");
    setText("budget-used", "$—");
    setText("budget-used-pct", "—%");
    setText("gauge-readout", "Set a budget to enable the gauge");
    setGaugeNeedle(0);
    $("budget-progress").style.width = "0%";
    return;
  }

  const remaining = Math.max(limit - totalSpend, 0);
  const pct = Math.min((totalSpend / limit) * 100, 999);

  setText("budget-remaining", formatCurrency(remaining));
  setText("budget-used", formatCurrency(totalSpend));
  setText("budget-used-pct", `${pct.toFixed(0)}%`);
  setText("gauge-readout", `${pct.toFixed(0)}% of budget used`);

  // Gauge needle maps 0-100% across the full arc; overflow past 100% pins at the end.
  setGaugeNeedle(pct);

  $("budget-progress").style.width = `${Math.min(pct, 100)}%`;
  $("budget-progress").style.background =
    pct >= 100 ? "rgba(255,91,107,0.65)" : pct >= 80 ? "rgba(244,195,90,0.65)" : "rgba(39,199,132,0.55)";
};

const donutColors = [
  "rgba(106,168,255,0.70)",
  "rgba(39,199,132,0.70)",
  "rgba(244,195,90,0.70)",
  "rgba(255,91,107,0.70)",
  "rgba(176,130,255,0.70)",
  "rgba(255,255,255,0.35)",
];

const renderDonut = (services) => {
  const svg = $("donut");
  const legend = $("donut-legend");
  if (!svg || !legend) return;

  svg.innerHTML = "";
  legend.innerHTML = "";

  const byCat = new Map();
  for (const s of services) {
    const k = s.businessLabel || "Other";
    byCat.set(k, (byCat.get(k) || 0) + (Number(s.cost) || 0));
  }

  const items = Array.from(byCat.entries()).sort((a, b) => b[1] - a[1]);
  const total = items.reduce((sum, [, v]) => sum + v, 0);

  if (!total || total < 0.00001) {
    const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    ring.setAttribute("cx", "60");
    ring.setAttribute("cy", "60");
    ring.setAttribute("r", "42");
    ring.setAttribute("fill", "none");
    ring.setAttribute("stroke", "rgba(255,255,255,0.12)");
    ring.setAttribute("stroke-width", "14");
    svg.appendChild(ring);
    legend.innerHTML = `<div class="mutedText small">No spend yet.</div>`;
    return;
  }

  const top = items.slice(0, 5);
  const rest = items.slice(5);
  const restSum = rest.reduce((sum, [, v]) => sum + v, 0);
  const final = restSum > 0 ? [...top, ["Other", restSum]] : top;

  const cx = 60, cy = 60, r = 42;
  const circumference = 2 * Math.PI * r;

  let offset = 0;
  final.forEach(([label, value], i) => {
    const frac = value / total;
    const dash = frac * circumference;

    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("cx", String(cx));
    c.setAttribute("cy", String(cy));
    c.setAttribute("r", String(r));
    c.setAttribute("fill", "none");
    c.setAttribute("stroke", donutColors[i % donutColors.length]);
    c.setAttribute("stroke-width", "14");
    c.setAttribute("stroke-dasharray", `${dash} ${circumference - dash}`);
    c.setAttribute("stroke-dashoffset", String(-offset));
    c.setAttribute("stroke-linecap", "round");
    c.dataset.index = String(i);
    c.style.transition = "opacity 0.15s ease";
    svg.appendChild(c);

    offset += dash;

    const row = document.createElement("div");
    row.className = "legendRow";
    row.dataset.index = String(i);
    row.innerHTML = `
      <div class="legendKey">
        <span class="swatch" style="background:${donutColors[i % donutColors.length]}"></span>
        <span>${label}</span>
      </div>
      <span>${formatCurrency(value)}</span>
    `;
    legend.appendChild(row);
  });

  // Hovering a legend row highlights its wedge and dims the rest — makes the
  // "where is my spend concentrated" question answerable at a glance.
  const legendRows = Array.from(legend.querySelectorAll(".legendRow"));
  const segments = Array.from(svg.querySelectorAll("circle[data-index]"));
  legendRows.forEach((row) => {
    row.addEventListener("mouseenter", () => {
      const idx = row.dataset.index;
      legendRows.forEach((r) => r.classList.toggle("dimmed", r.dataset.index !== idx));
      segments.forEach((s) => (s.style.opacity = s.dataset.index === idx ? "1" : "0.2"));
    });
    row.addEventListener("mouseleave", () => {
      legendRows.forEach((r) => r.classList.remove("dimmed"));
      segments.forEach((s) => (s.style.opacity = ""));
    });
  });

  const hole = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  hole.setAttribute("cx", "60");
  hole.setAttribute("cy", "60");
  hole.setAttribute("r", "28");
  hole.setAttribute("fill", "transparent");
  svg.appendChild(hole);
};

const renderDashboard = (data) => {
  const services = Array.isArray(data.services) ? data.services : [];
  const totalSpend = services.reduce((sum, item) => sum + (Number(item.cost) || 0), 0);

  setText("mtd-total", formatCurrency(totalSpend));
  setText("mtd-period", `Period: ${data.period || "—"}`);

  const averageDailySpend = totalSpend / 30;
  const forecast = averageDailySpend * 30;
  setText("forecast-total", formatCurrency(forecast));

  const prev = data.weekOverWeek?.previousWeek || 0;
  const curr = data.weekOverWeek?.currentWeek || 0;
  const weekChange = prev > 0 ? ((curr - prev) / prev) * 100 : 0;
  setText("wow-change", formatPercent(weekChange));

  const alerts = $("alerts");
  alerts.innerHTML = "";
  const triggered = thresholds.filter((limit) => totalSpend >= limit);
  if (triggered.length === 0) alerts.appendChild(createAlert("Spend is below all alert thresholds.", "ok"));
  else triggered.forEach((limit) => alerts.appendChild(createAlert(`Alert: Month-to-date spend exceeded ${formatCurrency(limit)}.`)));

  if (weekChange > 15) {
    alerts.appendChild(createAlert(`Spend increased ${formatPercent(weekChange)} week-over-week. Investigate anomalies.`));
  }

  const table = $("service-table");
  if (table) {
    table.innerHTML = "";
    services.forEach((service) => {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td>${service.service || "—"}</td>
        <td>${service.businessLabel || "Other"}</td>
        <td>${service.usage ? service.usage.toLocaleString() : "—"} ${service.unit || ""}</td>
        <td>${formatCurrency(Number(service.cost) || 0)}</td>
      `;
      table.appendChild(row);
    });
  }

  const bars = $("category-bars");
  bars.innerHTML = "";
  const byLabel = new Map();
  for (const s of services) {
    const k = s.businessLabel || "Other";
    byLabel.set(k, (byLabel.get(k) || 0) + (Number(s.cost) || 0));
  }
  const cats = Array.from(byLabel.entries()).sort((a, b) => b[1] - a[1]);
  const maxCost = Math.max(...cats.map(([, v]) => v), 1);

  cats.slice(0, 4).forEach(([label, cost]) => {
    const row = document.createElement("div");
    row.classList.add("bar-row");
    const percent = (cost / maxCost) * 100;
    row.innerHTML = `
      <span>${label}</span>
      <div class="bar"><span style="width:${percent}%"></span></div>
      <strong>${formatCurrency(cost)}</strong>
    `;
    bars.appendChild(row);
  });

  renderBudgetCard(totalSpend);
  renderDonut(services);
};

// ---------------------------
// Mapping editor (tab)
// ---------------------------
let currentMapping = {};

const escapeHtml = (s) =>
  String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

const renderMappingTable = () => {
  const body = $("map-body");
  if (!body) return;
  body.innerHTML = "";

  const entries = Object.entries(currentMapping).sort((a, b) => a[0].localeCompare(b[0]));
  for (const [svc, label] of entries) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input data-k="svc" value="${escapeHtml(svc)}" /></td>
      <td><input data-k="label" value="${escapeHtml(label)}" /></td>
      <td><button class="btn ghost" data-action="del">Delete</button></td>
    `;
    body.appendChild(tr);
  }

  body.querySelectorAll('button[data-action="del"]').forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const tr = e.target.closest("tr");
      const svc = tr.querySelector('input[data-k="svc"]').value.trim();
      if (svc && currentMapping[svc]) delete currentMapping[svc];
      tr.remove();
      setText("map-msg", "Row removed (not saved yet).");
    });
  });
};

const collectMappingFromTable = () => {
  const body = $("map-body");
  const out = {};
  if (!body) return out;

  body.querySelectorAll("tr").forEach((tr) => {
    const svc = tr.querySelector('input[data-k="svc"]').value.trim();
    const label = tr.querySelector('input[data-k="label"]').value.trim();
    if (svc && label) out[svc] = label;
  });

  return out;
};

// ---------------------------
// Navigation (tabs)
// ---------------------------
const showView = (name) => {
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.querySelectorAll(".navItem").forEach((b) => b.classList.remove("active"));

  $(`view-${name}`)?.classList.add("active");
  document.querySelector(`.navItem[data-view="${name}"]`)?.classList.add("active");
};

// ---------------------------
// Refresh
// ---------------------------
const refresh = async () => {
  const mode = getMode();
  setText("live-msg", "");

  try {
    if (mode === "sample") {
      setConnected(false, "Using sample data.");
      $("api-base-display").textContent = "Sample mode";
      const data = await loadSample();
      renderDashboard(data);
      setText("last-updated", new Date().toLocaleString());
      return;
    }

    // LIVE mode
    if (!API_BASE) API_BASE = await loadConfig();
    $("api-base-display").textContent = API_BASE;

    // Health ping
    const health = await fetch(`${API_BASE}/health?t=${Date.now()}`, { cache: "no-store" });
    if (!health.ok) throw new Error(`Health check failed ${health.status}`);
    setConnected(true);

    // Load mapping (for mapping tab)
    try {
      const mapResp = await loadMapping(API_BASE);
      currentMapping = mapResp.mapping || {};
      renderMappingTable();
      setText("map-msg", `Loaded ${Object.keys(currentMapping).length} mappings.`);
    } catch (e) {
      setText("map-msg", `Mapping load skipped: ${e.message || e}`);
    }

    const data = await loadSnapshot(API_BASE);
    renderDashboard(data);

    setText("last-updated", new Date().toLocaleString());
    setText("live-msg", "Live AWS snapshot loaded. (If values are $0.00, that’s normal for new/free-tier accounts.)");
  } catch (e) {
    setConnected(false, "");
    setText("live-msg", e.message || String(e));

    // Fallback: sample
    try {
      const data = await loadSample();
      renderDashboard(data);
    } catch {
      // ignore
    }
  }
};

// ---------------------------
// Boot
// ---------------------------
document.addEventListener("DOMContentLoaded", async () => {
  initTheme();

  // Theme
  $("theme-system")?.addEventListener("click", () => applyTheme("system"));
  $("theme-light")?.addEventListener("click", () => applyTheme("light"));
  $("theme-dark")?.addEventListener("click", () => applyTheme("dark"));

  // Nav
  document.querySelectorAll(".navItem").forEach((btn) => {
    btn.addEventListener("click", () => {
      showView(btn.dataset.view);
      $("sidebar")?.classList.remove("open");
      $("nav-scrim")?.classList.remove("open");
    });
  });

  // Mobile drawer
  $("nav-toggle")?.addEventListener("click", () => {
    $("sidebar")?.classList.add("open");
    $("nav-scrim")?.classList.add("open");
  });
  $("nav-scrim")?.addEventListener("click", () => {
    $("sidebar")?.classList.remove("open");
    $("nav-scrim")?.classList.remove("open");
  });

  // Mode
  setMode(getMode());
  $("btn-live")?.addEventListener("click", async () => {
    setMode("live");
    await refresh();
  });
  $("btn-sample")?.addEventListener("click", async () => {
    setMode("sample");
    await refresh();
  });
  $("btn-refresh")?.addEventListener("click", async () => {
    await refresh();
  });

  // Budget
  const budgetInput = $("budget-limit");
  if (budgetInput) {
    const saved = getBudgetLimit();
    budgetInput.value = saved ? String(saved) : "50";
  }
  $("budget-save")?.addEventListener("click", () => {
    const n = Number(budgetInput.value);
    if (!Number.isFinite(n) || n <= 0) {
      setText("live-msg", "Budget must be a positive number.");
      return;
    }
    setBudgetLimit(Math.round(n));
    setText("live-msg", "Budget saved.");
    // Re-render budget card if we already have totals on screen
    // (We simply refresh quickly in current mode)
    refresh();
  });

  // Copy API
  $("api-copy")?.addEventListener("click", async () => {
    const v = cleanBase($("api-base-display")?.textContent || "");
    if (!v || v === "Sample mode") return;
    try {
      await navigator.clipboard.writeText(v);
      setText("live-msg", "Copied.");
      setTimeout(() => setText("live-msg", ""), 800);
    } catch {
      setText("live-msg", "Copy failed. Select + Ctrl+C.");
    }
  });

  // Open Mapping tab button
  $("open-mapping")?.addEventListener("click", () => showView("mapping"));

  // Mapping tab controls
  $("map-add")?.addEventListener("click", () => {
    const body = $("map-body");
    if (!body) return;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input data-k="svc" placeholder="e.g., AWS Lambda" /></td>
      <td><input data-k="label" placeholder="e.g., Serverless" /></td>
      <td><button class="btn ghost" data-action="del">Delete</button></td>
    `;
    body.appendChild(tr);
    tr.querySelector('button[data-action="del"]').addEventListener("click", () => tr.remove());
    setText("map-msg", "Row added (not saved yet).");
  });

  $("map-save")?.addEventListener("click", async () => {
    const mode = getMode();
    if (mode !== "live") {
      setText("map-msg", "Switch to Live mode to save mapping.");
      return;
    }
    try {
      if (!API_BASE) API_BASE = await loadConfig();
      const token = ($("admin-token")?.value || "").trim();
      if (!token) {
        setText("map-msg", "Admin token required to save mapping.");
        return;
      }
      const mapping = collectMappingFromTable();
      await saveMapping(API_BASE, token, mapping);
      setText("map-msg", "Saved mapping successfully.");
    } catch (e) {
      setText("map-msg", e.message || String(e));
    }
  });

  // Initial refresh
  await refresh();
});
