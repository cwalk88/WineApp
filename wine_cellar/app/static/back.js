"use strict";

// Back button: return to the Origami dashboard's Wine cellar pop-up.
// The app runs in Home Assistant's ingress iframe on the same origin, so it
// asks the Home Assistant frontend to navigate in place (no full reload), the
// way Home Assistant's own links do. If that is not possible, the link's
// normal target="_top" navigation is used instead.
(() => {
  const link = document.getElementById("back-home");
  if (!link) return;
  link.addEventListener("click", (event) => {
    let top;
    try {
      top = window.top;
      if (top === window || !top.document.querySelector("home-assistant")) return;
    } catch {
      return; // different origin: let the link navigate normally
    }
    event.preventDefault();
    const url = new URL(link.getAttribute("href"), top.location.origin);
    top.history.pushState(null, "", url.pathname);
    top.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
    // Open the pop-up once the dashboard has rendered. Setting the hash fires
    // hashchange/popstate, which Bubble Card listens for.
    setTimeout(() => { top.location.hash = url.hash; }, 400);
  });
})();
