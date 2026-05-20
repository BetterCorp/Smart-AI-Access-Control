document.addEventListener("htmx:responseError", (event) => {
  console.error("HTMX request failed", event);
});

function refreshLiveFragments(eventName: string): void {
  document.querySelectorAll<HTMLElement>(`[data-live-event="${eventName}"]`).forEach(async (element) => {
    const url = element.dataset.liveUrl;
    if (!url) return;
    const response = await fetch(url, { headers: { "HX-Request": "true" } });
    if (!response.ok) return;
    const html = await response.text();
    if (element.dataset.liveSwap === "innerHTML") {
      element.innerHTML = html;
    } else {
      element.outerHTML = html;
    }
  });
}

function connectLiveStream(): void {
  const source = new EventSource("/api/live/stream");
  ["monitors", "monitor_outputs", "monitor_debug", "events", "relays", "health"].forEach((eventName) => {
    source.addEventListener(eventName, () => refreshLiveFragments(eventName));
  });
}

document.addEventListener("DOMContentLoaded", connectLiveStream);

function syncZoneInputs(editor: HTMLElement, x: number, y: number, width: number, height: number): void {
  const form = editor.closest("form");
  if (!form) return;
  const values = { zone_x: x, zone_y: y, zone_width: width, zone_height: height };
  Object.entries(values).forEach(([name, value]) => {
    const input = form.querySelector<HTMLInputElement>(`input[name="${name}"]`);
    if (input) input.value = value.toFixed(4);
  });
  const enabled = form.querySelector<HTMLInputElement>('input[name="zone_enabled"]');
  if (enabled && width > 0 && height > 0) enabled.checked = true;
}

function renderZone(editor: HTMLElement, x: number, y: number, width: number, height: number): void {
  const box = editor.querySelector<HTMLElement>(".zone-box");
  if (!box) return;
  box.style.left = `${x * 100}%`;
  box.style.top = `${y * 100}%`;
  box.style.width = `${width * 100}%`;
  box.style.height = `${height * 100}%`;
  box.hidden = width <= 0 || height <= 0;
  syncZoneInputs(editor, x, y, width, height);
}

function initZoneEditors(root: ParentNode = document): void {
  root.querySelectorAll<HTMLElement>("[data-zone-editor]").forEach((editor) => {
    if (editor.dataset.zoneReady === "true") return;
    editor.dataset.zoneReady = "true";
    renderZone(
      editor,
      Number(editor.dataset.zoneX || 0),
      Number(editor.dataset.zoneY || 0),
      Number(editor.dataset.zoneWidth || 0),
      Number(editor.dataset.zoneHeight || 0),
    );

    let startX = 0;
    let startY = 0;
    editor.addEventListener("pointerdown", (event) => {
      const rect = editor.getBoundingClientRect();
      startX = Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1);
      startY = Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1);
      editor.setPointerCapture(event.pointerId);
      renderZone(editor, startX, startY, 0, 0);
    });
    editor.addEventListener("pointermove", (event) => {
      if (!editor.hasPointerCapture(event.pointerId)) return;
      const rect = editor.getBoundingClientRect();
      const currentX = Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1);
      const currentY = Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1);
      const x = Math.min(startX, currentX);
      const y = Math.min(startY, currentY);
      renderZone(editor, x, y, Math.abs(currentX - startX), Math.abs(currentY - startY));
    });
    editor.addEventListener("pointerup", (event) => editor.releasePointerCapture(event.pointerId));
  });

  root.querySelectorAll<HTMLElement>("[data-clear-zone]").forEach((button) => {
    if (button.dataset.zoneReady === "true") return;
    button.dataset.zoneReady = "true";
    button.addEventListener("click", () => {
      const form = button.closest("form");
      const editor = form?.querySelector<HTMLElement>("[data-zone-editor]");
      if (!form || !editor) return;
      renderZone(editor, 0, 0, 0, 0);
      const enabled = form.querySelector<HTMLInputElement>('input[name="zone_enabled"]');
      if (enabled) enabled.checked = false;
    });
  });
}

document.addEventListener("DOMContentLoaded", () => initZoneEditors());
document.addEventListener("htmx:afterSwap", (event) => initZoneEditors(event.target as ParentNode));

function syncRuleMetricSelect(form: HTMLFormElement): void {
  const monitor = form.querySelector<HTMLSelectElement>("[data-rule-monitor-select]");
  const metric = form.querySelector<HTMLSelectElement>("[data-rule-metric-select]");
  if (!monitor || !metric) return;
  let selectedStillVisible = false;
  Array.from(metric.options).forEach((option) => {
    const visible = option.dataset.monitorId === monitor.value;
    option.hidden = !visible;
    option.disabled = !visible;
    if (visible && option.selected) selectedStillVisible = true;
  });
  if (!selectedStillVisible) {
    const first = Array.from(metric.options).find((option) => !option.disabled);
    if (first) metric.value = first.value;
  }
}

function initRuleDesigners(root: ParentNode = document): void {
  root.querySelectorAll<HTMLFormElement>(".rule-designer").forEach((form) => {
    if (form.dataset.ruleReady === "true") return;
    form.dataset.ruleReady = "true";
    syncRuleMetricSelect(form);
    form.querySelector("[data-rule-monitor-select]")?.addEventListener("change", () => syncRuleMetricSelect(form));
  });
}

document.addEventListener("DOMContentLoaded", () => initRuleDesigners());
document.addEventListener("htmx:afterSwap", (event) => initRuleDesigners(event.target as ParentNode));
