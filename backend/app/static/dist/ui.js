// backend/app/static/src/ui.ts
document.addEventListener("htmx:responseError", (event) => {
  console.error("HTMX request failed", event);
});
function refreshLiveFragments(eventName) {
  document.querySelectorAll(`[data-live-event="${eventName}"]`).forEach(async (element) => {
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
function connectLiveStream() {
  const source = new EventSource("/api/live/stream");
  ["monitor_outputs", "monitor_debug", "events", "relays", "health"].forEach((eventName) => {
    source.addEventListener(eventName, () => refreshLiveFragments(eventName));
  });
}
document.addEventListener("DOMContentLoaded", connectLiveStream);
