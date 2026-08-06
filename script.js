let rows = [...document.querySelectorAll("#rows tr")];
let dashboardData = null;
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

const formatBeijing = value => {
  if (!value) return "未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(date).replaceAll("/", "-");
};

const shortTime = value => {
  if (!value) return "--:--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(date);
};

const marketDateKey = value => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).formatToParts(date);
  const get = type => parts.find(part => part.type === type)?.value || "";
  return `${get("year")}-${get("month")}-${get("day")}`;
};

const shortMarketDate = dateKey => {
  const date = new Date(`${dateKey}T00:00:00+08:00`);
  if (Number.isNaN(date.getTime())) return dateKey;
  const day = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    weekday: "short"
  }).format(date);
  return `${dateKey.slice(5)} ${day}`;
};

const isStale = value => {
  const date = new Date(value);
  return !Number.isNaN(date.getTime()) && Date.now() - date.getTime() > 18 * 60 * 60 * 1000;
};

function asOfInfo(key, fallbackBasis = "最近一次成功更新") {
  const entry = dashboardData?.as_of?.[key] || {};
  const time = entry.time || dashboardData?.generated_at;
  return {
    time,
    stale: isStale(time),
    text: `截至 ${formatBeijing(time)}（北京时间） · ${entry.basis || fallbackBasis}`
  };
}

function setAsOf(elementId, key, fallbackBasis) {
  const node = document.querySelector(`#${elementId}`);
  if (!node) return;
  const info = asOfInfo(key, fallbackBasis);
  node.textContent = info.text;
  node.classList.toggle("stale", info.stale);
}

function bindSearch() {
  rows = [...document.querySelectorAll("#rows tr")];
}

function renderAggregation() {
  if (!dashboardData) return;
  const hasDailySubscription = Boolean(dashboardData.daily_subscription_status?.available);
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
        <td class="${hasDailySubscription ? colorClass(item.net_subscription_5d_yi) : ""}">${hasDailySubscription ? signed(item.net_subscription_5d_yi) : "待回填"}</td>
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

function flowBucket(item) {
  const name = `${item.index_name || ""} ${item.theme_family || ""}`.toLowerCase();
  if (item.market_scope === "跨境") return "跨境产品";
  if ((item.theme_level1 || "").includes("宽基")) return "宽基指数";
  if (/黄金9999|商品|原油|豆粕|有色期货/.test(name)) return "商品ETF";
  if (/债|货币|现金|同业存单|利率/.test(name)) return "债券及现金ETF";
  return "行业与策略主题";
}

function renderFlowStructure() {
  const order = ["宽基指数", "行业与策略主题", "跨境产品", "债券及现金ETF", "商品ETF"];
  const totals = Object.fromEntries(order.map(name => [name, 0]));
  (dashboardData?.indices || []).forEach(item => {
    totals[flowBucket(item)] += Number(item.estimated_flow_yi || 0);
  });
  const maxAbs = Math.max(1, ...Object.values(totals).map(value => Math.abs(value)));
  const list = document.querySelector("#flow-structure-list");
  if (list) {
    list.innerHTML = order.map(name => {
      const value = totals[name];
      const width = Math.max(2, Math.abs(value) / maxAbs * 100);
      const barColor = value >= 0 ? "#dc4040" : "#15966e";
      return `<div><span>${name} <b class="${colorClass(value)}">${signed(value, "亿元")}</b></span><i style="--w:${width.toFixed(1)}%;--bar-color:${barColor}"></i></div>`;
    }).join("");
  }
  const total = Number(dashboardData?.metrics?.estimated_flow_yi || 0);
  const totalNode = document.querySelector("#flow-structure-total");
  if (totalNode) {
    totalNode.textContent = signed(total);
    totalNode.className = colorClass(total);
  }
}

function renderActivity() {
  const items = [...(dashboardData?.themes || [])]
    .sort((a, b) => Math.abs(Number(b.estimated_flow_yi || 0)) - Math.abs(Number(a.estimated_flow_yi || 0)))
    .slice(0, 3);
  const info = asOfInfo("activity", "当前资金流截面观察");
  const list = document.querySelector("#activity-list");
  if (!list) return;
  list.innerHTML = items.map(item => `
    <div>
      <time>${shortTime(info.time)}</time>
      <span><strong>${item.theme_family || "未分类主题"}</strong><small>${item.etf_count || 0} 只ETF · ${item.index_count || 0} 个原指数 · 当前截面</small></span>
      <em class="${colorClass(item.estimated_flow_yi)}">${signed(item.estimated_flow_yi)}</em>
    </div>`).join("") || `<div><span><strong>尚无动态主题数据</strong><small>运行更新任务后生成</small></span></div>`;
}

function dailySubscriptionHistory() {
  return [...(dashboardData?.daily_subscription_history || [])]
    .filter(item => item.date)
    .sort((a, b) => String(a.date).localeCompare(String(b.date)))
    .map(item => ({
      date: item.date,
      flow: Number(item.net_subscription_yi || 0),
      etfCount: Number(item.etf_count || 0)
    }));
}

function renderDailyFlow() {
  const daily = dailySubscriptionHistory();
  const week = daily.slice(-5);
  const previous = daily.length ? daily[daily.length - 1] : null;
  const previousDate = document.querySelector("#previous-day-date");
  const previousFlow = document.querySelector("#previous-day-flow");
  if (previous) {
    previousDate.textContent = previous.date;
    previousFlow.textContent = signed(previous.flow);
    previousFlow.className = colorClass(previous.flow);
  } else {
    previousDate.textContent = "历史覆盖不足";
    previousFlow.textContent = "--";
    previousFlow.className = "";
  }

  const maxAbs = Math.max(1, ...week.map(item => Math.abs(item.flow)));
  const list = document.querySelector("#daily-flow-list");
  if (list) {
    list.innerHTML = week.map(item => {
      const width = Math.max(2, Math.abs(item.flow) / maxAbs * 50);
      const direction = item.flow >= 0 ? "positive" : "negative";
      return `<div class="daily-flow-row">
        <time>${shortMarketDate(item.date)}</time>
        <span class="daily-axis"><i class="${direction}" style="--w:${width.toFixed(1)}%"></i></span>
        <strong class="${colorClass(item.flow)}">${signed(item.flow)}</strong>
      </div>`;
    }).join("") || `<div class="daily-flow-empty">尚无历史数据</div>`;
  }

  const total = week.reduce((sum, item) => sum + item.flow, 0);
  const totalNode = document.querySelector("#weekly-flow-total");
  if (totalNode) {
    totalNode.textContent = week.length
      ? `近${week.length}个有效交易日合计 ${signed(total)}`
      : "待回填";
    totalNode.className = week.length ? colorClass(total) : "";
  }
  const coverage = document.querySelector("#daily-flow-coverage");
  if (coverage) {
    coverage.textContent = week.length
      ? `展示 ${week[0].date} 至 ${week[week.length - 1].date}；最新日覆盖 ${previous.etfCount} 只ETF`
      : (dashboardData?.daily_subscription_status?.message || "等待iFinD历史份额与净值回填");
  }
}

function renderTrend(days) {
  const allDaily = dailySubscriptionHistory();
  const now = new Date();
  const cutoff = new Date(now);
  cutoff.setDate(cutoff.getDate() - days + 1);
  const cutoffKey = cutoff.toISOString().slice(0, 10);
  const selected = allDaily.filter(item => item.date >= cutoffKey);
  const total = selected.reduce((sum, item) => sum + item.flow, 0);
  const totalNode = document.querySelector("#trend-total");
  totalNode.innerHTML = selected.length
    ? `${signed(total)} <small>区间累计净申购</small>`
    : `-- <small>等待iFinD历史净申赎</small>`;
  totalNode.className = selected.length ? colorClass(total) : "";

  const coverage = document.querySelector("#trend-coverage");
  if (!selected.length) {
    coverage.textContent = "当前区间尚无历史数据";
  } else if (selected.length < Math.min(days, 20)) {
    coverage.textContent =
      `历史覆盖不足：仅 ${selected.length} 个交易日，自 ${selected[0].date} 起；等待iFinD补齐历史净申赎`;
  } else {
    coverage.textContent =
      `${selected[0].date} 至 ${selected[selected.length - 1].date} · ETF份额变化 × 当日单位净值`;
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

  setAsOf("asof-turnover", "turnover", "当日累计成交额");
  setAsOf("asof-flow", "intraday_flow", "成交方向资金流估算");
  setAsOf("asof-change", "price_change", "ETF行情等权涨跌幅");
  setAsOf("asof-aum", "aum", "行情总市值口径");
  setAsOf("asof-trend", "trend", "iFinD日度净申购赎回");
  setAsOf("asof-daily-flow", "daily_flow", "iFinD日度净申购赎回");
  setAsOf("asof-flow-structure", "flow_structure", "盘中行情快照");
  setAsOf("asof-aggregation", "aggregation", "盘中行情快照");
  setAsOf("asof-activity", "activity", "当前资金流截面观察");
  const headerInfo = asOfInfo("intraday_flow", "最近一次成功更新");
  const headerNode = document.querySelector("#header-asof");
  if (headerNode) headerNode.textContent = `最近行情 ${formatBeijing(headerInfo.time)}（北京时间）`;

  renderAggregation();
  renderFlowStructure();
  renderActivity();
  renderDailyFlow();

  const managers = (data.managers || []).slice(0, 12).map((item, index) => `
    <div>
      <b>${index + 1}</b>
      <span><strong>${item.manager || "待补充管理人"}</strong><small>${item.etf_count || 0} 只 ETF · 规模 ${number(item.aum_yi)} 亿</small></span>
      <i style="--w:${Math.min(100, item.bar_width || 0)}%"></i>
      <em class="${colorClass(item.estimated_flow_yi)}">${signed(item.estimated_flow_yi)}<small>盘中估算</small><small class="${data.daily_subscription_status?.available ? colorClass(item.net_subscription_5d_yi) : ""}">${data.daily_subscription_status?.available ? `5日申赎 ${signed(item.net_subscription_5d_yi)}` : "5日申赎待回填"}</small></em>
    </div>`).join("");
  if (managers) {
    const managerInfo = asOfInfo("managers", "盘中行情快照");
    document.querySelector("#manager-list").innerHTML =
      `<div class="panel-head"><div><h3>管理人资金流竞争</h3><small id="asof-managers" class="section-asof ${managerInfo.stale ? "stale" : ""}">${managerInfo.text}</small><small>盘中估算与近5日净申赎分列</small></div></div>${managers}`;
  }

  const generated = data.generated_at
    ? new Date(data.generated_at).toLocaleString("zh-CN", { hour12: false })
    : "未知";
  const dailyStatus = data.daily_subscription_status?.available
    ? `日度净申赎已覆盖至 ${data.daily_subscription_status.last_date}`
    : `日度净申赎：${data.daily_subscription_status?.message || "等待回填"}`;
  document.querySelector("#data-status").textContent =
    `数据范围：沪深上市 ETF · 页面数据文件生成：${generated} · ${dailyStatus} · 各板块实际截至时间见板块标题 · 数据源：${data.source_label || "自动采集"} · 历史起点：${data.history_start || "首次运行后生成"}`;
  bindSearch();
}

async function loadData() {
  try {
    const stamp = Date.now();
    const dashboardResponse = await fetch(
      `data/dashboard.json?t=${stamp}`, { cache: "no-store" }
    );
    if (!dashboardResponse.ok) throw new Error(`dashboard HTTP ${dashboardResponse.status}`);
    dashboardData = await dashboardResponse.json();
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
