"use strict";

// All URLs are relative so the app works behind Home Assistant ingress
// (/api/hassio_ingress/<token>/).

const STYLES = {
  red: "Red", white: "White", rose: "Rosé", sparkling: "Sparkling",
  dessert: "Dessert", fortified: "Fortified", orange: "Orange",
};

const state = {
  status: null,
  racks: [],
  bottles: [],
  leds: { slots: [], color: "" },
  query: "",
  style: "",
  picked: null,        // slot chosen in the check-in picker
  movingBottle: null,  // bottle being moved: next empty-slot tap moves it
  draft: null,         // { photo, wine } from the label analysis
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: {} };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join(", ")
      : data.detail;
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return data;
}

let toastTimer;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), isError ? 6000 : 3500);
}

async function run(fn) {
  try {
    return await fn();
  } catch (e) {
    toast(e.message, true);
  }
}

// ---------------------------------------------------------------- helpers

const rackById = (id) => state.racks.find((r) => r.id === id);
const styleColor = (style) => `var(--st-${STYLES[style] ? style : "red"})`;

function where(b) {
  if (b.status !== "in") return "Checked out";
  const rack = rackById(b.rack_id);
  return `${rack ? rack.name : "Rack"} · row ${b.slot_row + 1}, col ${b.slot_col + 1}`;
}

function title(b) {
  return `${b.name}${b.vintage ? " " + b.vintage : ""}`;
}

function thumb(b, cls = "thumb") {
  if (b.photo) return `<img class="${cls}" src="api/photos/${encodeURIComponent(b.photo)}" alt="" loading="lazy">`;
  return `<span class="${cls}"><span class="dot" style="background:${styleColor(b.style)};width:18px;height:18px"></span></span>`;
}

function matches(b) {
  if (state.style && b.style !== state.style) return false;
  if (!state.query) return true;
  const hay = [b.name, b.producer, b.region, b.country, b.vintage, b.notes, b.pairing_summary,
    ...(b.grapes || []), ...(b.food_pairings || [])].join(" ").toLowerCase();
  return state.query.toLowerCase().split(/\s+/).every((t) => hay.includes(t));
}

const litKey = (rackId, row, col) => `${rackId}:${row}:${col}`;
function litSet() {
  return new Set(state.leds.slots.map((s) => litKey(s.rack_id, s.row, s.col)));
}

// ---------------------------------------------------------------- loading

async function refresh() {
  const [status, racks, bottles, leds] = await Promise.all([
    api("api/status"), api("api/racks"), api("api/bottles?status=in"), api("api/leds"),
  ]);
  Object.assign(state, { status, racks, bottles, leds });
  render();
}

async function pollLeds() {
  try {
    const leds = await api("api/leds");
    const changed = JSON.stringify(leds) !== JSON.stringify(state.leds);
    state.leds = leds;
    if (changed) { renderHeader(); renderCellar(); }
  } catch { /* offline for a moment; try again next tick */ }
}

function render() {
  renderHeader();
  renderCellar();
  if (!$("#add-form").hidden) renderSlotPicker();
}

function renderHeader() {
  const s = state.status;
  if (!s) return;
  const free = s.capacity - s.stats.in;
  $("#summary").textContent = state.racks.length
    ? `${s.stats.in} bottle${s.stats.in === 1 ? "" : "s"} · ${free} free slot${free === 1 ? "" : "s"}`
    : "Set up a rack to get started";

  const chip = $("#led-chip");
  const lit = state.leds.slots.length;
  chip.className = "chip";
  chip.style.background = "";
  if (state.leds.error) {
    chip.classList.add("err");
    chip.textContent = "LED error";
    chip.title = state.leds.error;
  } else if (lit) {
    chip.classList.add("lit");
    chip.style.background = `#${state.leds.color}`;
    chip.textContent = `${lit} slot${lit === 1 ? "" : "s"} lit`;
    chip.title = "";
  } else {
    chip.textContent = s.led_driver === "none" ? "LEDs: preview only" : `LEDs: ${s.led_driver}`;
    chip.title = "";
  }
  $("#lights-off").hidden = !lit;
}

// ---------------------------------------------------------------- rack grids

/**
 * Draw every rack as a grid of slots.
 * opts.mode: "cellar" (tap filled slot = open bottle) | "pick" (tap empty slot = choose it)
 */
function rackGrids(container, opts) {
  const lit = litSet();
  const glow = `#${state.leds.color || "ffb000"}`;
  const bySlot = new Map(state.bottles.map((b) => [litKey(b.rack_id, b.slot_row, b.slot_col), b]));
  const filtering = opts.mode === "cellar" && (state.query || state.style);

  container.innerHTML = state.racks.map((rack) => {
    const used = state.bottles.filter((b) => b.rack_id === rack.id).length;
    let cells = "";
    for (let r = 0; r < rack.rows; r++) {
      for (let c = 0; c < rack.cols; c++) {
        const key = litKey(rack.id, r, c);
        const b = bySlot.get(key);
        const classes = ["slot"];
        let style = "";
        let label = `Row ${r + 1}, col ${c + 1}: empty`;
        if (b) {
          classes.push("filled");
          style = `background:${styleColor(b.style)};`;
          label = `Row ${r + 1}, col ${c + 1}: ${title(b)}`;
          if (filtering && !matches(b)) classes.push("dim");
        } else if (filtering) classes.push("dim");
        if (opts.mode === "cellar" && lit.has(key)) { classes.push("lit"); style += `--glow:${glow};`; }
        if (opts.mode === "pick" && state.picked && state.picked.rack_id === rack.id
            && state.picked.row === r && state.picked.col === c) classes.push("picked");
        cells += `<button type="button" class="${classes.join(" ")}" style="${style}" title="${esc(label)}"
          aria-label="${esc(label)}" data-rack="${rack.id}" data-row="${r}" data-col="${c}"
          ${b ? `data-bottle="${b.id}"` : ""}></button>`;
      }
    }
    return `<div class="rack">
      <h3>${esc(rack.name)} <span>${used}/${rack.rows * rack.cols}</span></h3>
      <div class="rack-scroll"><div class="rack-grid" style="grid-template-columns:repeat(${rack.cols}, max-content)">${cells}</div></div>
    </div>`;
  }).join("") || `<div class="empty-state">No racks yet. Add one on the <a href="#" data-goto="racks">Racks</a> tab.</div>`;
}

// ---------------------------------------------------------------- cellar view

function renderCellar() {
  const filters = $("#style-filters");
  const present = new Set(state.bottles.map((b) => b.style));
  filters.innerHTML = [`<button data-style="" class="${state.style ? "" : "active"}">All</button>`,
    ...Object.entries(STYLES).filter(([k]) => present.has(k)).map(([k, v]) =>
      `<button data-style="${k}" class="${state.style === k ? "active" : ""}">
        <span class="dot" style="background:${styleColor(k)}"></span>${v}</button>`),
  ].join("");

  rackGrids($("#racks-visual"), { mode: "cellar" });

  const list = state.bottles.filter(matches);
  const lit = new Set(state.leds.slots.map((s) => s.bottle_id));
  $("#bottle-count").textContent = list.length === state.bottles.length
    ? `(${list.length})` : `(${list.length} of ${state.bottles.length})`;
  $("#light-matches").hidden = !(state.query || state.style) || !list.length;
  $("#bottle-list").innerHTML = list.map((b) => bottleCard(b, lit.has(b.id))).join("")
    || `<div class="empty-state">${state.bottles.length ? "No bottles match." : "No bottles yet. Use Check in to add your first."}</div>`;
}

function bottleCard(b, isLit = false) {
  const sub = [b.producer, [b.region, b.country].filter(Boolean).join(", ")].filter(Boolean).join(" · ");
  return `<button class="bottle ${isLit ? "lit" : ""}" style="--glow:#${state.leds.color || "ffb000"}" data-bottle="${b.id}">
    ${thumb(b)}
    <span class="meta">
      <div class="name">${esc(title(b))}</div>
      <div class="sub">${esc(sub || STYLES[b.style] || "")}</div>
      <div class="loc">${esc(where(b))}</div>
    </span>
  </button>`;
}

// ---------------------------------------------------------------- bottle dialog

async function openBottle(id, { locate = true } = {}) {
  const b = await api(`api/bottles/${id}`);
  const dlg = $("#bottle-dialog");
  const facts = [
    ["Style", STYLES[b.style]],
    ["Grapes", (b.grapes || []).join(", ")],
    ["Region", [b.region, b.country].filter(Boolean).join(", ")],
    ["Drink", b.drink_from || b.drink_until ? `${b.drink_from || "?"} – ${b.drink_until || "?"}` : ""],
    ["Serve", b.serving_temp_c ? `${b.serving_temp_c} °C` : ""],
    ["Decant", b.decant_minutes ? `${b.decant_minutes} min` : ""],
    ["ABV", b.abv ? `${b.abv}%` : ""],
  ].filter(([, v]) => v);

  dlg.innerHTML = `<div class="dlg">
    <div class="dlg-head">
      ${thumb(b, "thumb-lg")}
      <div>
        <h2>${esc(title(b))}</h2>
        <p class="muted">${esc(b.producer)}</p>
      </div>
    </div>
    <div class="where"><span class="dot" style="background:#${b.status === "in" ? state.status.colors.locate : "999"}"></span>${esc(where(b))}</div>
    ${b.food_pairings?.length ? `<div><h3>Goes with</h3><div class="tags">${b.food_pairings.map((f) => `<span class="tag">${esc(f)}</span>`).join("")}</div></div>` : ""}
    ${b.pairing_summary ? `<p>${esc(b.pairing_summary)}</p>` : ""}
    ${b.tasting_notes ? `<p class="muted">${esc(b.tasting_notes)}</p>` : ""}
    ${facts.length ? `<dl class="facts">${facts.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
    ${b.notes ? `<p><strong>Notes:</strong> ${esc(b.notes)}</p>` : ""}
    <div class="dlg-actions">
      <button class="btn ghost danger" data-act="delete">Delete</button>
      ${b.status === "in" ? `
        <button class="btn ghost" data-act="move">Move</button>
        <button class="btn ghost" data-act="locate">Light it</button>
        <button class="btn primary" data-act="checkout">Take out</button>` : `
        <button class="btn primary" data-act="return">Put back in rack</button>`}
      <button class="btn ghost" data-act="close">Close</button>
    </div>
  </div>`;

  dlg.onclick = (e) => {
    if (e.target === dlg) return dlg.close();
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act) bottleAction(act, b);
  };
  dlg.showModal();
  if (locate && b.status === "in") await run(() => api(`api/bottles/${b.id}/locate`, { method: "POST" })).then(pollLeds);
}

async function bottleAction(act, b) {
  const dlg = $("#bottle-dialog");
  if (act === "close") return dlg.close();
  if (act === "locate") {
    await run(() => api(`api/bottles/${b.id}/locate`, { method: "POST" }));
    return pollLeds();
  }
  if (act === "checkout") {
    await run(async () => {
      await api(`api/bottles/${b.id}/checkout`, { method: "POST" });
      dlg.close();
      toast(`Enjoy the ${title(b)} 🍷`);
      await refresh();
    });
  }
  if (act === "return") {
    await run(async () => {
      const nb = await api(`api/bottles/${b.id}/return`, { method: "POST", body: {} });
      dlg.close();
      toast(`Put it in ${where(nb)}`);
      await refresh();
      renderHistory();
    });
  }
  if (act === "move") {
    state.movingBottle = b;
    dlg.close();
    showView("cellar");
    toast("Tap an empty slot to move the bottle there");
  }
  if (act === "delete") {
    if (!confirm(`Delete ${title(b)} from the cellar? This can't be undone.`)) return;
    await run(async () => {
      await api(`api/bottles/${b.id}`, { method: "DELETE" });
      dlg.close();
      await refresh();
      renderHistory();
    });
  }
}

// ---------------------------------------------------------------- check in

function resetAdd() {
  state.draft = null;
  state.picked = null;
  $("#add-step-photo").hidden = false;
  $("#add-step-analyzing").hidden = true;
  $("#add-form").hidden = true;
  $("#add-form").reset();
  $("#photo-input").value = "";
}

/** Shrink the photo before upload: phone photos are big and Claude only needs ~1600px. */
async function downscale(file, maxSide = 1600) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((resolve, reject) => {
      const i = new Image();
      i.onload = () => resolve(i);
      i.onerror = () => reject(new Error("Couldn't read that image"));
      i.src = url;
    });
    const scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.naturalWidth * scale);
    canvas.height = Math.round(img.naturalHeight * scale);
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function onPhoto(file) {
  $("#add-step-photo").hidden = true;
  $("#add-step-analyzing").hidden = false;
  const preview = $("#analyzing-preview");
  preview.src = URL.createObjectURL(file);
  try {
    const blob = await downscale(file);
    const form = new FormData();
    form.append("image", blob, "label.jpg");
    const result = await api("api/analyze", { method: "POST", form });
    state.draft = result;
    showForm(result.wine, result.photo, result.error);
  } catch (e) {
    toast(e.message, true);
    resetAdd();
  }
}

function showForm(wine, photo, error = "") {
  const form = $("#add-form");
  $("#add-step-photo").hidden = true;
  $("#add-step-analyzing").hidden = true;
  form.hidden = false;

  const errEl = $("#analyze-error");
  errEl.hidden = !error && !(wine && !wine.is_wine);
  errEl.textContent = error || "That doesn't look like a wine label - fill in the details below.";

  const img = $("#form-photo");
  img.hidden = !photo;
  if (photo) img.src = `api/photos/${encodeURIComponent(photo)}`;
  $("#confidence").textContent = wine && wine.is_wine
    ? `Identified by Claude · ${wine.confidence} confidence. Check the details before saving.` : "";

  if (wine && wine.is_wine) {
    for (const [k, v] of Object.entries(wine)) {
      const el = form.elements[k];
      if (!el || v == null) continue;
      el.value = Array.isArray(v) ? v.join(", ") : v;
    }
  }
  state.picked = null;
  renderSlotPicker();
  form.elements.name.focus();
}

function renderSlotPicker() {
  rackGrids($("#slot-picker"), { mode: "pick" });
  $("#slot-hint").textContent = state.picked
    ? `Chosen: ${rackById(state.picked.rack_id)?.name} · row ${state.picked.row + 1}, col ${state.picked.col + 1} (tap again to clear)`
    : "Next free slot is chosen automatically. Tap an empty slot to pick one yourself.";
}

async function submitCheckIn(e) {
  e.preventDefault();
  const f = e.target.elements;
  const num = (v) => (v === "" ? null : Number(v));
  const list = (v) => v.split(",").map((s) => s.trim()).filter(Boolean);
  const body = {
    name: f.name.value.trim(),
    producer: f.producer.value.trim(),
    vintage: num(f.vintage.value),
    style: f.style.value,
    grapes: list(f.grapes.value),
    region: f.region.value.trim(),
    country: f.country.value.trim(),
    drink_from: num(f.drink_from.value),
    drink_until: num(f.drink_until.value),
    food_pairings: list(f.food_pairings.value),
    pairing_summary: f.pairing_summary.value.trim(),
    tasting_notes: f.tasting_notes.value.trim(),
    serving_temp_c: f.serving_temp_c.value.trim(),
    decant_minutes: num(f.decant_minutes.value),
    notes: f.notes.value.trim(),
    abv: state.draft?.wine?.abv ?? null,
    photo: state.draft?.photo ?? null,
    quantity: Number(f.quantity.value) || 1,
    ...(state.picked || {}),
  };
  await run(async () => {
    const added = await api("api/bottles", { method: "POST", body });
    await refresh();
    const places = added.map(where);
    toast(added.length === 1 ? `Put it in ${places[0]}` : `${added.length} bottles: ${places.join("; ")}`);
    resetAdd();
    showView("cellar");
  });
}

// ---------------------------------------------------------------- sommelier

async function submitPair(e) {
  e.preventDefault();
  const meal = $("#meal").value.trim();
  if (!meal) return;
  const out = $("#pair-result");
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  out.innerHTML = `<div class="card center"><div class="spinner"></div><p>Asking the sommelier…</p></div>`;
  try {
    const r = await api("api/pair", { method: "POST", body: { meal } });
    out.innerHTML = `<div class="card">
      <p>${esc(r.advice)}</p>
      ${r.picks.map((p, i) => `<div class="pick">
        <span class="rank">${i + 1}</span>
        <div style="flex:1">${bottleCard(p.bottle, true)}<p>${esc(p.reason)}</p></div>
      </div>`).join("")}
    </div>`;
    pollLeds();
  } catch (err) {
    out.innerHTML = `<div class="card error">${esc(err.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------- racks

// Mirrors layout.py so the wiring preview updates while you type.
function slotLeds(g, row, col) {
  const r = g.start_corner.startsWith("top") ? row : g.rows - 1 - row;
  const c = g.start_corner.endsWith("left") ? col : g.cols - 1 - col;
  let [run, pos, perRun] = g.orientation === "horizontal" ? [r, c, g.cols] : [c, r, g.rows];
  if (g.serpentine && run % 2 === 1) pos = perRun - 1 - pos;
  const runLen = perRun * g.leds_per_slot + g.leds_between_runs;
  const base = g.led_start + run * runLen + pos * g.leds_per_slot;
  return Array.from({ length: g.leds_per_slot }, (_, i) => base + i);
}

function readRackForm(form) {
  const f = form.elements;
  return {
    name: f.name.value.trim(),
    rows: Number(f.rows.value),
    cols: Number(f.cols.value),
    leds_per_slot: Number(f.leds_per_slot.value),
    led_start: f.led_start.value === "" ? null : Number(f.led_start.value),
    start_corner: f.start_corner.value,
    orientation: f.orientation.value,
    serpentine: f.serpentine.checked,
    leds_between_runs: Number(f.leds_between_runs.value) || 0,
  };
}

function drawLedMap(form, rackId) {
  const g = readRackForm(form);
  const map = $(".led-map", form);
  if (!(g.rows >= 1 && g.cols >= 1 && g.rows <= 100 && g.cols <= 100 && g.leds_per_slot >= 1)) {
    map.innerHTML = "";
    return;
  }
  if (g.led_start == null) g.led_start = 0;
  const runs = g.orientation === "horizontal" ? g.rows : g.cols;
  const total = runs * (g.orientation === "horizontal" ? g.cols : g.rows) * g.leds_per_slot
    + (runs - 1) * g.leds_between_runs;
  $(".led-range", form).textContent =
    `Uses LEDs ${g.led_start}–${g.led_start + total - 1} (${total} LEDs). Numbers show the first LED of each slot; tap a slot to light it.`;
  let cells = "";
  for (let r = 0; r < g.rows; r++) {
    for (let c = 0; c < g.cols; c++) {
      cells += `<button type="button" class="slot" data-test-row="${r}" data-test-col="${c}"
        ${rackId ? "" : "disabled"}><span class="led-num">${slotLeds(g, r, c)[0]}</span></button>`;
    }
  }
  map.innerHTML = `<div class="rack-grid" style="grid-template-columns:repeat(${g.cols}, max-content)">${cells}</div>`;
}

function rackEditor(rack) {
  const form = $("#rack-editor-tpl").content.firstElementChild.cloneNode(true);
  const f = form.elements;
  const r = rack || {
    name: `Rack ${state.racks.length + 1}`, rows: 6, cols: 8, leds_per_slot: 1, led_start: "",
    start_corner: "top_left", orientation: "horizontal", serpentine: false, leds_between_runs: 0,
  };
  for (const k of ["name", "rows", "cols", "leds_per_slot", "led_start", "start_corner", "orientation", "leds_between_runs"]) {
    f[k].value = r[k] ?? "";
  }
  f.serpentine.checked = !!r.serpentine;
  if (!rack) {
    $("[data-act=delete]", form).textContent = "Cancel";
    $("[data-act=test]", form).hidden = true;
  }

  form.addEventListener("input", () => drawLedMap(form, rack?.id));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    run(async () => {
      const body = readRackForm(form);
      if (rack) await api(`api/racks/${rack.id}`, { method: "PATCH", body });
      else await api("api/racks", { method: "POST", body });
      toast("Rack saved");
      await refresh();
      renderRacks();
    });
  });
  form.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    const slot = e.target.closest("[data-test-row]");
    if (slot && rack) {
      run(() => api("api/leds/test", {
        method: "POST",
        body: { rack_id: rack.id, row: Number(slot.dataset.testRow), col: Number(slot.dataset.testCol) },
      })).then(pollLeds);
    }
    if (act === "test") run(() => api(`api/racks/${rack.id}/test`, { method: "POST" })).then(pollLeds);
    if (act === "delete") {
      if (!rack) return form.remove();
      if (!confirm(`Delete ${rack.name}?`)) return;
      run(async () => {
        await api(`api/racks/${rack.id}`, { method: "DELETE" });
        await refresh();
        renderRacks();
      });
    }
  });
  drawLedMap(form, rack?.id);
  return form;
}

function renderRacks() {
  const host = $("#rack-editors");
  host.innerHTML = "";
  state.racks.forEach((r) => host.appendChild(rackEditor(r)));
}

// ---------------------------------------------------------------- history

async function renderHistory() {
  const [out, events] = await Promise.all([api("api/bottles?status=out"), api("api/events?limit=60")]);
  $("#out-list").innerHTML = out.map((b) => bottleCard(b)).join("")
    || `<div class="empty-state">Nothing checked out.</div>`;
  const verbs = { check_in: "Checked in", check_out: "Took out", move: "Moved", delete: "Deleted" };
  $("#events").innerHTML = events.map((e) => `<li>
      <time>${new Date(e.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</time>
      <span><strong>${verbs[e.action] || esc(e.action)}</strong> ${esc(e.name ? title(e) : "a bottle")}
      ${e.detail ? `<span class="muted">· ${esc(e.detail)}</span>` : ""}</span>
    </li>`).join("") || `<li class="muted">No activity yet.</li>`;
}

// ---------------------------------------------------------------- navigation + events

function showView(name) {
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (name === "racks") renderRacks();
  if (name === "history") run(renderHistory);
  if (name === "add" && !state.racks.length) toast("Add a rack first so the bottle has somewhere to go", true);
  if (name !== "cellar") state.movingBottle = null;
}

function bind() {
  $(".tabs").addEventListener("click", (e) => {
    const v = e.target.closest("button")?.dataset.view;
    if (v) showView(v);
  });
  document.addEventListener("click", (e) => {
    const go = e.target.closest("[data-goto]");
    if (go) { e.preventDefault(); showView(go.dataset.goto); }
  });

  $("#search").addEventListener("input", (e) => { state.query = e.target.value.trim(); renderCellar(); });
  $("#style-filters").addEventListener("click", (e) => {
    const b = e.target.closest("[data-style]");
    if (b) { state.style = b.dataset.style; renderCellar(); }
  });
  $("#light-matches").addEventListener("click", () => run(async () => {
    const ids = state.bottles.filter(matches).map((b) => b.id);
    const r = await api("api/locate", { method: "POST", body: ids });
    toast(`Lit ${r.lit} slot${r.lit === 1 ? "" : "s"}`);
    pollLeds();
  }));
  $("#lights-off").addEventListener("click", () => run(async () => {
    await api("api/leds/clear", { method: "POST" });
    pollLeds();
  }));

  // Cellar: tap a bottle (card or slot) to open it; tap an empty slot while moving.
  $("#view-cellar").addEventListener("click", (e) => {
    const target = e.target.closest("[data-bottle], .slot");
    if (!target) return;
    if (target.dataset.bottle) return run(() => openBottle(Number(target.dataset.bottle)));
    if (state.movingBottle && target.dataset.rack) {
      const b = state.movingBottle;
      state.movingBottle = null;
      run(async () => {
        const moved = await api(`api/bottles/${b.id}/move`, {
          method: "POST",
          body: { rack_id: Number(target.dataset.rack), row: Number(target.dataset.row), col: Number(target.dataset.col) },
        });
        toast(`Moved to ${where(moved)}`);
        await refresh();
      });
    }
  });
  $("#out-list").addEventListener("click", (e) => {
    const t = e.target.closest("[data-bottle]");
    if (t) run(() => openBottle(Number(t.dataset.bottle), { locate: false }));
  });
  $("#pair-result").addEventListener("click", (e) => {
    const t = e.target.closest("[data-bottle]");
    if (t) run(() => openBottle(Number(t.dataset.bottle), { locate: false }));
  });

  // Check in
  $("#photo-input").addEventListener("change", (e) => e.target.files[0] && onPhoto(e.target.files[0]));
  $("#manual-entry").addEventListener("click", () => { state.draft = null; showForm(null, null); });
  $("#add-cancel").addEventListener("click", resetAdd);
  $("#add-form").addEventListener("submit", submitCheckIn);
  $("#slot-picker").addEventListener("click", (e) => {
    const s = e.target.closest(".slot");
    if (!s) return;
    if (s.dataset.bottle) return toast("That slot is taken - pick an empty one", true);
    const pick = { rack_id: Number(s.dataset.rack), row: Number(s.dataset.row), col: Number(s.dataset.col) };
    const same = state.picked && state.picked.rack_id === pick.rack_id
      && state.picked.row === pick.row && state.picked.col === pick.col;
    state.picked = same ? null : pick;
    renderSlotPicker();
    if (state.picked) {
      run(() => api("api/leds/test", { method: "POST", body: pick })).then(pollLeds);
    }
  });

  // Sommelier
  $("#pair-form").addEventListener("submit", submitPair);

  // Racks
  $("#add-rack").addEventListener("click", () => {
    $("#rack-editors").prepend(rackEditor(null));
  });
}

bind();
run(refresh);
setInterval(pollLeds, 4000);
