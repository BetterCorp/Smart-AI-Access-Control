// backend/app/static/src/ui.ts
document.addEventListener("htmx:responseError", (event) => {
  console.error("HTMX request failed", event);
});
