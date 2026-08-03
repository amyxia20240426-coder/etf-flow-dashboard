let rows = [...document.querySelectorAll("#rows tr")];
let dashboardData = null;
let historyData = [];
let aggregationView = "themes";

const search = document.querySelector("#search");
const navButtons = [...document.querySelectorAll("nav button")];
const pageTitle = document.querySelector("h1");

const number = (value, digits = 1) =>
  Number(value || 0).toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  });

const signed = (value, suffix = " 亿") => {
  const amount = Number(value || 0);
  return `${amount >= 0 ? "+" : "−"}${number(Math.abs(amount))}${suffix}`;
};

const colorClass = value => Number(value || 0) >= 0 ? "up" : "down";

function bindSearch() {
  rows = [...document.querySelectorAll("#rows tr")];
}

function renderAggregation() {
  if (!dashboardData) return;
  const isTheme = aggregationView === "themes";
  const items = (isTheme ? dashboardData.themes : dashboardData.indices) || [];
  const indexCount = (dashboardData.indices || []).length;
  const etfCount = Number(dashboardData.metrics?.etf_count || 0);

  document.querySelector("#aggregation-title").textContent =
    isTheme ? "相似投向 ETF 主题聚合" : "同指数 ETF 精确聚合";
  document.querySelector("#aggregation-note").textContent = isTheme
    ? "跨管理人、跨相近指数合并 · 标记“待审核”的主题请结合审核表确认"
    : "按指数提供商 + 指数代码/原名称精确归组";
  document.querySelector("#group-column-title").textContent = isTheme ? "主题族群" : "跟踪指数";

  const rendered = items.slice(0, 150).map((item, index) => {
    const title = isTheme ? item.theme_family : item.index_name;
    const secondary = isTheme
      ? `${item.theme_level1 || "待审核"} · ${item.index_count || 0} 个原指数${item.review_required ? " · 待审核" : ""}`
      : (item.index_code || item.index_provider || "待补充指数代码");
    const key = isTheme
      ? `${title} ${item.theme_level1 || ""} ${(item.exact_indices || []).join(" ")}`
      : `${title} ${item.index_code || ""} ${item.index_provider || ""}`;
    return `
      <tr data-key="${key}">
        <td><b class="rank ${index === 0 ? "one" : index === 1 ? "two" : index === 2 ? "three" : ""}">${index + 1}</b></td>
        <td><strong>${title || "未分类"}</strong><small>${secondary}</small></td>
        <td>${item.etf_count || 0} 只<small>${item.manager_count || 0} 家管理人</small></td>
        <td>${number(item.aum_yi)} 亿</td>
        <td>${number(item.turnover_yi)} 亿</td>
        <td class="${colorClass(item.estimated_flow_yi)}">${signed(item.estimated_flow_yi)}</td>
        <td class="${colorClass(item.flow_5d_yi)}">${signed(item.flow_5d_yi)}</td>
        <td><i class="intensity" style="--w:${Math.min(100, Math.abs(item.flow_strength || 0))}%"></i></td>
      </tr>`;
  }).join("");
  document.querySelector("#rows").innerHTML =
    rendered || `<tr><td colspan="8">尚无${isTheme ? "主题" : "指数"}聚合数据，请先运行一次更新任务。</td></tr>`;
  document.querySelector("#table-foot").innerHTML = isTheme
    ? `共 ${items.length} 个主题族群 · ${indexCount} 个原指数 · ${etfCount.toLocaleString("zh-CN")} 只 ETF <span>主题归类保留原指数明细</span>`
    : `共 ${indexCount} 个原指数 · ${etfCount.toLocaleString("zh-CN")} 只 ETF <span>精确指数层不做相似性合并</span>`;
  rows = [...document.querySelectorAll("#rows tr")];
}

function dailyHistory(raw) {
  const latestByDate = new Map();
  [...(raw || [])]
    .sort((a, b) => String(a.observed_at).localeCompare(String(b.observed_at)))
    .forEach(item => {
      if (!item.observed_at) return;
      latestByDate.set(item.observed_at.slice(0, 10), item);
    });
  return [...latestByDate.entries()].map(([date, item]) => ({
    date,
    flow: Number(item.estimated_flow_yi || 0)
  }));
}

function renderTrend(days) {
  const allDaily = dailyHistory(historyData);
  const now = new Date();
  const cutoff = new Date(now);
  cutoff.setDate(cutoff.getDate() - days + 1);
  const cutoffKey = cutoff.toISOString().slice(0, 10);
  const selected = allDaily.filter(item => item.date >= cutoffKey);
  const total = selected.reduce((sum, item) => sum + item.flow, 0);
  const totalNode = document.querySelector("#trend-total");
  totalNode.innerHTML = `${signed(total)} <small>区间累计净流入</small>`;
  totalNode.className = colorClass(total);

  const coverage = document.querySelector("#trend-coverage");
  if (!selected.length) {
    coverage.textContent = "当前区间尚无历史数据";
  } else if (selected.length < Math.min(days, 20)) {
    coverage.textContent =
      `历史覆盖不足：仅 ${selected.length} 个交易日，自 ${selected[0].date} 起；系统会随每次更新自动累积`;
  } else {
    coverage.textContent =
      `${selected[0].date} 至 ${selected[selected.length - 1].date} · 日末估算净流入累计`;
  }

  const cumulative = [];
  selected.reduce((sum, item) => {
    const value = sum + item.flow;
    cumulative.push({ date: item.date, value });
    return value;
  }, 0);

  const line = document.querySelector("#trend-line");
  const area = document.querySelector("#trend-area");
  if (!cumulative.length) {
    line.setAttribute("d", "");
    area.setAttribute("d", "");
    return;
  }
  const chartPoints = cumulative.length === 1
    ? [cumulative[0], cumulative[0]]
    : cumulative;
  const values = chartPoints.map(item => item.value);
  let minValue = Math.min(0, ...values);
  let maxValue = Math.max(0, ...values);
  if (minValue === maxValue) {
    minValue -= 1;
    maxValue += 1;
  }
  const x = index => 35 + (695 * index / Math.max(1, chartPoints.length - 1));
  const y = value => 205 - ((value - minValue) / (maxValue - minValue)) * 180;
  const path = chartPoints
    .map((item, index) => `${index ? "L" : "M"}${x(index).toFixed(1)} ${y(item.value).toFixed(1)}`)
    .join(" ");
  line.setAttribute("d", path);
  area.setAttribute("d", `${path}V215H35Z`);
}

function renderDashboard(data) {
  dashboardData = data;
  const metrics = data.metrics || {};
  document.querySelector("#metric-turnover").innerHTML = `${number(metrics.turnover_yi)}<small>亿元</small>`;
  document.querySelector("#metric-count").textContent = `共 ${Number(metrics.etf_count || 0).toLocaleString("zh-CN")} 只`;
  document.querySelector("#metric-flow").innerHTML = `${signed(metrics.estimated_flow_yi, "")}<small>亿元</small>`;
  document.querySelector("#metric-flow").className = colorClass(metrics.estimated_flow_yi);
  document.querySelector("#metric-inflow-count").textContent = `净流入 ${Number(metrics.inflow_count || 0).toLocaleString("zh-CN")} 只`;
  document.querySelector("#metric-change").innerHTML = `${signed(metrics.average_change_pct, "")}<small>%</small>`;
  document.querySelector("#metric-change").className = colorClass(metrics.average_change_pct);
  document.querySelector("#metric-aum").innerHTML = `${number((metrics.aum_yi || 0) / 10000, 2)}<small>万亿元</small>`;
  document.querySelector("#metric-source").textContent = data.source_label || "自动采集";

  renderAggregation();

  const managers = (data.managers || []).slice(0, 12).map((item, index) => `
    <div>
      <b>${index + 1}</b>
      <span><strong>${item.manager || "待补充管理人"}</strong><small>${item.etf_count || 0} 只 ETF · 规模 ${number(item.aum_yi)} 亿</small></span>
      <i style="--w:${Math.min(100, item.bar_width || 0)}%"></i>
      <em class="${colorClass(item.estimated_flow_yi)}">${signed(item.estimated_flow_yi)}</em>
    </div>`).join("");
  if (managers) {
    document.querySelector("#manager-list").innerHTML =
      `<div class="panel-head"><div><h3>管理人资金流竞争</h3><small>今日全产品合计</small></div></div>${managers}`;
  }

  const generated = data.generated_at
    ? new Date(data.generated_at).toLocaleString("zh-CN", { hour12: false })
    : "未知";
  document.querySelector("#data-status").textContent =
    `数据范围：沪深上市 ETF · 更新时间：${generated} · 数据源：${data.source_label || "自动采集"} · 历史起点：${data.history_start || "首次运行后生成"}`;
  bindSearch();
}

async function loadData() {
  try {
    const stamp = Date.now();
    const [dashboardResponse, historyResponse] = await Promise.all([
      fetch(`data/dashboard.json?t=${stamp}`, { cache: "no-store" }),
      fetch(`data/market_history.json?t=${stamp}`, { cache: "no-store" })
    ]);
    if (!dashboardResponse.ok) throw new Error(`dashboard HTTP ${dashboardResponse.status}`);
    dashboardData = await dashboardResponse.json();
    historyData = historyResponse.ok ? await historyResponse.json() : [];
    renderDashboard(dashboardData);
    renderTrend(365);
  } catch (error) {
    document.querySelector("#data-status").textContent =
      `数据更新文件尚未生成，暂时显示页面内置内容。${error.message}`;
  }
}

const navTargets = [
  { title: "市场总览", selector: ".metrics" },
  { title: "指数聚合", selector: ".table-card" },
  { title: "管理人竞争", selector: ".managers" },
  { title: "ETF 明细", selector: ".table-card", focusSearch: true },
  { title: "数据状态", selector: ".footnote" }
];

navButtons.forEach((button, index) => {
  button.addEventListener("click", () => {
    const target = navTargets[index];
    navButtons.forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    if (pageTitle) pageTitle.textContent = target.title;
    document.querySelector(target.selector)?.scrollIntoView({ behavior: "smooth", block: "start" });
    if (target.focusSearch) window.setTimeout(() => search?.focus(), 500);
  });
});

document.querySelectorAll(".period button").forEach(button => {
  button.addEventListener("click", () => {
    button.parentElement.querySelectorAll("button").forEach(item => item.classList.remove("on"));
    button.classList.add("on");
    renderTrend(Number(button.dataset.days || 365));
  });
});

document.querySelectorAll("#aggregation-tabs button").forEach(button => {
  button.addEventListener("click", () => {
    button.parentElement.querySelectorAll("button").forEach(item => item.classList.remove("on"));
    button.classList.add("on");
    aggregationView = button.dataset.view || "themes";
    renderAggregation();
  });
});

search?.addEventListener("input", () => {
  const term = search.value.trim().toLowerCase();
  rows.forEach(row => {
    row.hidden = !(row.dataset.key || "").toLowerCase().includes(term);
  });
});

loadData();
