const search = document.querySelector("#search");
const rows = [...document.querySelectorAll("#rows tr")];
search?.addEventListener("input", () => {
  const term = search.value.trim().toLowerCase();
  rows.forEach(row => row.hidden = !row.dataset.key.toLowerCase().includes(term));
});

document.querySelectorAll(".period button,.tabs button").forEach(button => {
  button.addEventListener("click", () => {
    button.parentElement.querySelectorAll("button").forEach(item => item.classList.remove("on"));
    button.classList.add("on");
  });
});
