const search = document.querySelector("#search");
const rows = [...document.querySelectorAll("#rows tr")];
const navButtons = [...document.querySelectorAll("nav button")];
const pageTitle = document.querySelector("h1");

search?.addEventListener("input", () => {
  const term = search.value.trim().toLowerCase();
  rows.forEach(row => row.hidden = !row.dataset.key.toLowerCase().includes(term));
});

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
    document.querySelector(target.selector)?.scrollIntoView({
      behavior: "smooth",
      block: "start"
    });
    if (target.focusSearch) {
      window.setTimeout(() => search?.focus(), 500);
    }
  });
});

document.querySelectorAll(".period button,.tabs button").forEach(button => {
  button.addEventListener("click", () => {
    button.parentElement.querySelectorAll("button").forEach(item => item.classList.remove("on"));
    button.classList.add("on");
  });
});
