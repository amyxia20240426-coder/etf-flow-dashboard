let rows = [...document.querySelectorAll("#rows tr")];
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
  const apply = () => {
    const term = search?.value.trim().toLowerCase() || "";
    rows.forEach(row => {
      row.hidden = !(row.dataset.key || "").toLowerCase().includes(term);
    });
  };
  search?.removeEventListener("input", apply);
  search?.addEventListener("input", apply);
}

function renderDashboard(data) {
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

  const indexRows = (data.indices || []).slice(0, 100).map((item, index) => `
    <tr data-key="${item.index_name} ${item.index_code || ""}">
      <td><b class="rank ${index === 0 ? "one" : index === 1 ? "two" : index === 2 ? "three" : ""}">${index + 1}</b></td>
      <td><strong>${item.index_name || "未分类"}</strong><small>${item.index_code || item.index_provider || "待补充指数代码"}</small></td>
      <td>${item.etf_count || 0} 只<small>${item.manager_count || 0} 家管理人</small></td>
      <td>${number(item.aum_yi)} 亿</td>
      <td>${number(item.turnover_yi)} 亿</td>
      <td class="${colorClass(item.estimated_flow_yi)}">${signed(item.estimated_flow_yi)}</td>
      <td class="${colorClass(item.flow_5d_yi)}">${signed(item.flow_5d_yi)}</td>
      <td><i class="intensity" style="--w:${Math.min(100, Math.abs(item.flow_strength || 0))}%"></i></td>
    </tr>`).join("");
  if (indexRows) document.querySelector("#rows").innerHTML = indexRows;

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
    const response = await fetch(`data/dashboard.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderDashboard(await response.json());
  } catch (error) {
    document.querySelector("#data-status").textContent =
      `数据更新文件尚未生成，暂时显示演示数据。${error.message}`;
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

document.querySelectorAll(".period button,.tabs button").forEach(button => {
  button.addEventListener("click", () => {
    button.parentElement.querySelectorAll("button").forEach(item => item.classList.remove("on"));
    button.classList.add("on");
  });
});

bindSearch();
loadData();
