/* DejaFix staff pages: progressive enhancement only. Every page works without this file.
   - Forms marked data-busy show a working state and can't be submitted twice.
   - Buttons marked data-copy copy a Meko ID to the clipboard. */
(() => {
  "use strict";
  document.documentElement.classList.add("js");

  const CLOCK_MS = 100;

  function startClock(panel) {
    const clock = panel.querySelector("[data-clock]");
    if (!clock) return;
    const started = performance.now();
    const tick = () => { clock.textContent = `${((performance.now() - started) / 1000).toFixed(1)} s`; };
    tick();
    window.setInterval(tick, CLOCK_MS);
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-busy")) return;
    if (form.dataset.submitted === "1") { event.preventDefault(); return; }
    form.dataset.submitted = "1";
    // Lock every busy form on the page: approve and reject must not race each other.
    document.querySelectorAll("form[data-busy] button[type=submit]").forEach((button) => {
      button.disabled = true;
      button.setAttribute("aria-disabled", "true");
    });
    const button = form.querySelector("button[type=submit]");
    if (button && button.dataset.busyText) {
      const label = button.querySelector("[data-label]") || button;
      label.textContent = button.dataset.busyText;
    }
    const panel = document.getElementById(form.dataset.busy || "");
    if (panel) {
      panel.hidden = false;
      startClock(panel);
    }
    form.setAttribute("aria-busy", "true");
  });

  // Live progress of a background run: poll the ticket's progress endpoint, then reload when done.
  const live = document.querySelector("[data-progress-url]");
  if (live) {
    const POLL_MS = 1000;
    const list = live.querySelector("[data-steps]");
    const clock = live.querySelector("[data-elapsed]");
    const stepItem = (step) => {
      const item = document.createElement("li");
      item.className = "done";
      item.innerHTML = '<svg class="icon icon-s" aria-hidden="true"><use href="#i-check"/></svg>';
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = step.label;
      const nums = document.createElement("span");
      nums.className = "nums";
      nums.textContent = `${Number(step.tokens).toLocaleString("en-US")} tokens · ${step.meko_calls} Meko calls`;
      item.append(name, nums);
      return item;
    };
    const render = (data) => {
      const next = list.querySelector(".next");
      list.querySelectorAll("li.done").forEach((item) => item.remove());
      data.steps.forEach((step) => list.insertBefore(stepItem(step), next));
      if (clock && typeof data.elapsed_s === "number") clock.textContent = `${data.elapsed_s.toFixed(1)} s`;
    };
    const poll = async () => {
      try {
        const response = await fetch(live.dataset.progressUrl, { headers: { Accept: "application/json" }, cache: "no-store" });
        if (response.ok) {
          const data = await response.json();
          render(data);
          if (data.done) { window.location.reload(); return; }
        }
      } catch (error) {
        // A dropped request is retried on the next tick.
      }
      window.setTimeout(poll, POLL_MS);
    };
    poll();
  }

  document.addEventListener("click", async (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-copy]") : null;
    if (!button) return;
    try {
      await navigator.clipboard.writeText(button.dataset.copy || "");
      button.classList.add("copied");
      button.setAttribute("aria-label", "Copied");
      window.setTimeout(() => { button.classList.remove("copied"); button.setAttribute("aria-label", "Copy"); }, 1600);
    } catch (error) {
      button.setAttribute("aria-label", "Copy failed");
    }
  });
})();
