async function postJson(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return await res.json();
}

function initDragDrop() {
  const lists = Array.from(document.querySelectorAll(".task-list"));
  if (lists.length === 0) return;

  const groupName = "tasks-board";

  for (const el of lists) {
    new Sortable(el, {
      group: groupName,
      animation: 150,
      draggable: ".task-card",
      ghostClass: "task-ghost",
      onAdd: async (evt) => {
        const status = evt.to.dataset.status;
        const card = evt.item;
        const taskId = card?.dataset?.taskId;
        if (!taskId || !status) return;

        try {
          await postJson(`/api/tasks/${taskId}/move`, { status });
        } catch (e) {
          alert("Не удалось переместить задачу. Обновите страницу и попробуйте снова.");
          window.location.reload();
        }
      },
    });
  }
}

function initMultiFilterDropdowns() {
  const dropdowns = Array.from(document.querySelectorAll("details[data-multi-filter]"));
  if (dropdowns.length === 0) return;

  const sync = (details) => {
    const summary = details.querySelector("summary");
    const label = details.dataset.label || "";
    const checks = Array.from(details.querySelectorAll("input[type=checkbox]"));
    const selected = checks.filter((c) => c.checked);

    let text = "Все";
    if (selected.length > 0) {
      const names = selected
        .map((c) => c.closest("label")?.querySelector(".form-check-label")?.textContent?.trim())
        .filter(Boolean);
      if (names.length <= 2) text = names.join(", ");
      else text = `${names.slice(0, 2).join(", ")} +${names.length - 2}`;
    }

    summary.textContent = label ? `${label}: ${text}` : text;
  };

  for (const d of dropdowns) {
    sync(d);
    d.addEventListener("change", () => sync(d));
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initDragDrop();
  initMultiFilterDropdowns();
});

