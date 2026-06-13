/* Plugin store: live search + tag filter */
(function () {
  const search = document.getElementById("plugin-search");
  const pills = document.getElementById("filter-pills");
  const grid = document.getElementById("plugin-grid");
  const empty = document.getElementById("empty-state");
  if (!search || !grid || !pills) return;

  const cards = Array.from(grid.querySelectorAll(".plugin-card"));
  let activeTag = "all";
  let query = "";

  function applyFilter() {
    let visibleCount = 0;
    cards.forEach((card) => {
      const tags = (card.getAttribute("data-tags") || "").toLowerCase();
      const name = (card.getAttribute("data-name") || "").toLowerCase();
      const desc = card.textContent.toLowerCase();
      const tagOK = activeTag === "all" || tags.includes(activeTag);
      const queryOK = !query || name.includes(query) || desc.includes(query) || tags.includes(query);
      const show = tagOK && queryOK;
      card.style.display = show ? "" : "none";
      if (show) visibleCount++;
    });
    if (empty) empty.style.display = visibleCount === 0 ? "block" : "none";
  }

  search.addEventListener("input", (e) => {
    query = e.target.value.trim().toLowerCase();
    applyFilter();
  });

  pills.addEventListener("click", (e) => {
    const btn = e.target.closest(".pill");
    if (!btn) return;
    pills.querySelectorAll(".pill").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    activeTag = btn.getAttribute("data-tag");
    applyFilter();
  });
})();
