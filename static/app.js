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

document.addEventListener("DOMContentLoaded", () => {
  initDragDrop();
});

