/* Karat Board — the screen. One fetch of /api/state paints everything. */

let STATE = null;
let UNIT = 1;               // 1 = per gram, 10 = per 10 grams
let manualFor = null;

/* No jeweller publishes what they will pay you back - it is the 24K rate minus
   a cut, 2-3% almost everywhere. Rather than a permanent extra block on every
   merchant, the cut is a filter: pick one and that merchant's 24K tile flips to
   the buyback; click the live chip again and it flips back to the rate.

   Deliberately NOT remembered. The board's resting state is the two rates with
   every filter off, so a reload always answers "what is gold today?" and never
   greets you with a buyback you switched on yesterday. */
const CUTS = {};
const cutFor = (id) => (CUTS[id] === 2 || CUTS[id] === 3) ? CUTS[id] : 0;

const $ = (id) => document.getElementById(id);
const money = (v) => v == null ? null :
  "₹" + Math.round(v * UNIT).toLocaleString("en-IN");

function snack(msg) {
  $("snackText").textContent = msg;
  $("snack").classList.add("show");
  clearTimeout(snack._t);
  snack._t = setTimeout(() => $("snack").classList.remove("show"), 2600);
}

/* ---- theme ---- */
const theme = localStorage.getItem("kb-theme") || "light";
document.documentElement.dataset.theme = theme;
$("themeBtn").onclick = () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("kb-theme", next);
};

/* ---- unit switch ---- */
document.querySelectorAll(".seg button").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".seg button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    UNIT = Number(b.dataset.unit);
    localStorage.setItem("kb-unit", UNIT);
    paint();
  };
});
{
  const saved = localStorage.getItem("kb-unit");
  if (saved === "10") document.querySelector('.seg button[data-unit="10"]').click();
}

/* ---- time helpers ---- */
function ago(iso) {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return mins + " min ago";
  const h = Math.round(mins / 60);
  if (h < 24) return h + (h === 1 ? " hour ago" : " hours ago");
  return Math.round(h / 24) + "d ago";
}
function clock(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-IN",
    { hour: "2-digit", minute: "2-digit", hour12: true });
}

/* ---- data ----
   Two homes, one page. Run locally and it talks to the Python; published to a
   static host there is no Python, so it reads the rates.json a scheduled build
   left behind. The board looks the same either way - only the buttons that need
   a backend go quiet. */
let STATIC = false;

async function load() {
  if (!STATIC) {
    try {
      const r = await fetch("/api/state");
      if (r.ok) { STATE = await r.json(); paint(); return; }
    } catch (e) { /* no backend here - fall through to the snapshot */ }
    STATIC = true;
    document.body.classList.add("is-static");
  }
  const r = await fetch("rates.json?t=" + Date.now());
  if (!r.ok) throw new Error("no rates.json");
  STATE = await r.json();
  paint();
}

async function refresh(id) {
  await fetch("/api/refresh", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(id ? { id } : {}),
  });
  snack(id ? "Re-reading that merchant…" : "Re-reading every merchant…");
  // A full pass walks eight sites politely; poll until it settles.
  for (let i = 0; i < 40; i++) {
    await new Promise((res) => setTimeout(res, 1500));
    await load();
    if (!STATE.refreshing) break;
  }
}
$("refreshBtn").onclick = () => refresh(null);

/* ---- painting ---- */
const has = (m) => (m.rate && (m.rate.buy24 || m.rate.buy22)) ? 1 : 0;

function paint() {
  if (!STATE) return;
  const ms = STATE.merchants;

  // The cheapest live 24K on the board, ignoring anything that failed.
  const live = ms.filter((m) => m.rate && m.rate.buy24);
  const best24 = live.length ? Math.min(...live.map((m) => m.rate.buy24)) : null;
  const live22 = ms.filter((m) => m.rate && m.rate.buy22);
  const best22 = live22.length ? Math.min(...live22.map((m) => m.rate.buy22)) : null;
  const high24 = live.length ? Math.max(...live.map((m) => m.rate.buy24)) : null;

  paintHeads(live, best24, best22, high24);

  // Merchants with no numbers sink to the bottom, so the rates you came for are
  // the first thing on screen. Within each group the merchants.json order holds
  // (Array#sort is stable).
  const ordered = [...ms].sort((a, b) => has(b) - has(a));
  $("board").innerHTML = "";
  ordered.forEach((m) => $("board").appendChild(card(m, best24)));

  const busy = STATE.refreshing;
  $("status").querySelector(".dot").className = "dot" + (busy ? " busy" : "");
  const line = busy ? "Reading merchants…"
    : "Updated " + ago(STATE.lastRefresh) +
      (STATIC ? " · refreshes every " : " · re-reads itself every ")
      + STATE.refreshMinutes + " minutes";
  $("status").title = line;
  $("statusText").textContent = line;
  $("refreshBtn").disabled = busy;
  $("refreshBtn").hidden = STATIC;
}

function paintHeads(live, best24, best22, high24) {
  const cheapest = live.find((m) => m.rate.buy24 === best24);
  const spread = (best24 != null && high24 != null) ? high24 - best24 : null;
  const heads = [
    { k: "Cheapest 24K", v: money(best24), w: cheapest ? cheapest.name : "no rate yet" },
    { k: "Cheapest 22K", v: money(best22),
      w: (() => { const c = STATE.merchants.find((m) => m.rate && m.rate.buy22 === best22);
                  return c ? c.name : "no rate yet"; })() },
    { k: "Spread across the board", v: spread == null ? null : money(spread),
      w: live.length + " of " + STATE.merchants.length + " merchants reporting" },
  ];
  $("heads").innerHTML = heads.map((h) => `
    <div class="head">
      <span class="k">${h.k}</span>
      <span class="v">${h.v || "—"}</span>
      <span class="w">${esc(h.w)}</span>
    </div>`).join("");
}

/* ---- Merchant marks ----
   Each merchant's own favicon was fetched at first, and eight logos in eight
   brand colours (Malabar maroon, PNG purple, BRPL orange…) fought the gold
   board. So every mark is redrawn here in ONE language: a 24x24 grid, the same
   stroke weight, all of it inheriting the board's bronze, with secondary strokes
   dropped to 45-55% so each mark has some depth rather than reading as wire.

   The SHAPE is the merchant's own, traced off their actual logo - Malabar's
   ringed M with the centre dot, Tanishq's flared T, Kalyan's twin ribbon sweeps,
   MMTC-PAMP's lettered coin, Aspect's notched block, PNG's interlocking bands,
   Bhima's serif B with its detached dot and swoosh. Only the colour is ours. */
const MARKS = {
  // ringed geometric M, dot in the counter
  malabar: `<circle cx="12" cy="12" r="9.5" opacity=".45"/>
            <path d="M9 15.9V8.3M15 15.9V8.3M9 8.3l3 3.6 3-3.6"/>
            <circle cx="12" cy="13.5" r=".95" fill="currentColor" stroke="none"/>`,
  // flared T over its dot
  tanishq: `<path d="M4.4 6.4c1.1-1.6 2.3-1.6 3.5-1.6h8.2c1.2 0 2.4 0 3.5 1.6"/>
            <path d="M9.7 5.1c0 6.7-1.3 10.9-4.5 13.8"/>
            <path d="M14.3 5.1c0 6.7 1.3 10.9 4.5 13.8"/>
            <circle cx="12" cy="18.7" r="1.45" fill="currentColor" stroke="none"/>`,
  // twin ribbon sweeps off a stem
  kalyan: `<path d="M6.4 3.6v16.8"/>
           <path d="M6.4 13.6C12.6 11 17.4 6.9 19.1 3.1"/>
           <path d="M7.1 13.1C12 15.3 17.5 17.8 19.5 21.2" opacity=".55"/>`,
  // the lettered coin rim
  mmtc: `<circle cx="12" cy="12" r="9.3"/><circle cx="12" cy="12" r="4.5" opacity=".9"/>
         <g opacity=".5" stroke-width="1.5">
           <path d="M18.4 12h1.7M16.5 16.5l1.2 1.2M12 18.4v1.7M7.5 16.5l-1.2 1.2"/>
           <path d="M5.6 12H3.9M7.5 7.5 6.3 6.3M12 5.6V3.9M16.5 7.5l1.2-1.2"/>
         </g>`,
  // notched corner block with its offset square
  aspect: `<path d="M9.2 4.4h10.4v15.2h-5.2V9.4H9.2z"/>
           <path d="M4.4 15.1h4.5v4.5H4.4z" opacity=".55"/>`,
  // R over the shoulder of a B
  brpl: `<path d="M6.5 5.2v14.2" opacity=".45"/>
         <path d="M9.4 19.4V5.2h4.2a3.7 3.7 0 0 1 0 7.4H9.4"/>
         <path d="m13.5 12.6 4.6 6.8"/>`,
  // two interlocking bands
  png: `<circle cx="9.2" cy="12" r="5.6"/><circle cx="14.8" cy="12" r="5.6"/>`,
  // serif B, detached dot, swoosh
  bhima: `<path d="M9 4.8v11.4"/>
          <path d="M9 4.8h3.4a2.8 2.8 0 0 1 0 5.6H9"/>
          <path d="M9 10.4h3.9a2.9 2.9 0 0 1 0 5.8H9"/>
          <circle cx="6.1" cy="8.9" r="1.25" fill="currentColor" stroke="none"/>
          <path d="M4.3 19.5c4.7-2.3 10.7-2.3 15.4 0" opacity=".55"/>`,
};

function mark(m) {
  const art = MARKS[m.id];
  if (!art) return esc(initials(m.short));
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"
               stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${art}</svg>`;
}

function card(m, best24) {
  const r = m.rate || {};
  const el = document.createElement("article");
  el.className = "card";
  if (m.note) el.title = m.note;
  const isBest = r.buy24 && r.buy24 === best24;
  if (isBest) el.classList.add("best");
  if (!r.buy24 && !r.buy22) el.classList.add("dim");

  let state = "off", why = "no automatic source";
  if (r.ok && r.manual) { state = "ok"; why = "keyed in by hand"; }
  else if (r.ok) { state = "ok"; why = "read from the site"; }
  else if (r.stale) { state = "stale"; why = "last good read — " + (r.error || "site changed"); }
  else if (r.error && !r.linkOnly) { state = "err"; why = r.error; }


  // Buyback is always reckoned on 24K - purity is what a buyback is priced off.
  // The 22K figure stays put; it is there so you know the counter price.
  const cut = cutFor(m.id);

  el.innerHTML = `
    ${isBest ? '<span class="badge">CHEAPEST 24K</span>' :
      r.manual ? '<span class="badge manual">MANUAL</span>' : ""}
    <div class="who">
      <span class="mark">${mark(m)}</span>
      <span class="nm"><b>${esc(m.name)}</b><small>${esc(why)}</small></span>
      <span class="state ${state}" title="${esc(why)}"></span>
    </div>

    <div class="rates">
      <div class="rate k24 ${cut && r.buy24 ? "buyback" : ""}">${k24Face(r, cut)}</div>
      <div class="rate">
        <span class="kt">22K ${r.derived22
          ? '<span class="drv" title="Derived: 24K x 22/24">DERIVED</span>' : ""}</span>
        ${r.buy22 ? `<div class="amt">${money(r.buy22)}</div>` : '<div class="none">—</div>'}
        <span class="per">${UNIT === 1 ? "per gram" : "per 10 g"}</span>
      </div>
    </div>

    ${r.buy24 ? `<div class="cut-row"><span class="cuts">
        <button data-cut="2" class="${cut === 2 ? "on" : ""}"
                title="Flip the 24K tile to what they would pay you, 2% under">2% cut</button>
        <button data-cut="3" class="${cut === 3 ? "on" : ""}"
                title="Flip the 24K tile to what they would pay you, 3% under">3% cut</button>
      </span></div>` : ""}

    ${m.spark && m.spark.length > 2 ? spark(m.spark) : ""}

    ${state === "err" ? `<div class="err-note">${esc(r.error || "")}</div>` : ""}

    <div class="foot">
      <span class="when">${(r.buy24 || r.buy22)
        ? "as of " + clock(r.fetched) + " · " + ago(r.fetched)
        : "no rate on the board yet"}</span>
      <span class="acts">
        <button title="Open ${esc(m.short)}" data-act="open">
          <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M14 3h7v7h-2V6.4l-8.3 8.3-1.4-1.4L17.6 5H14V3ZM5 5h5v2H6.5v10.5H17V14h2v5.5H5V5Z"/></svg>
        </button>
        ${STATIC ? "" : `<button title="Key in a rate by hand" data-act="manual">
          <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M4 20h4L18.5 9.5l-4-4L4 16v4Zm14.7-11.8 1.6-1.6a1.4 1.4 0 0 0 0-2l-2-2a1.4 1.4 0 0 0-2 0l-1.6 1.6 4 4Z"/></svg>
        </button>`}
        ${(!STATIC && m.adapter !== "link_only") ? `<button title="Re-read just this one" data-act="reload">
          <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M12 5V2L7 6l5 4V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7Z"/></svg>
        </button>` : ""}
      </span>
    </div>`;

  el.querySelectorAll(".cuts button").forEach((b) => {
    b.onclick = () => {
      const want = Number(b.dataset.cut);
      CUTS[m.id] = cutFor(m.id) === want ? 0 : want;   // clicking the live chip turns it off
      const now = cutFor(m.id);
      el.querySelectorAll(".cuts button")
        .forEach((x) => x.classList.toggle("on", Number(x.dataset.cut) === now));
      flipK24(el, r, now);
    };
  });
  el.querySelector('[data-act="open"]').onclick = () => window.open(m.site, "_blank", "noopener");
  const mn = el.querySelector('[data-act="manual"]');
  if (mn) mn.onclick = () => openManual(m);
  const rl = el.querySelector('[data-act="reload"]');
  if (rl) rl.onclick = () => refresh(m.id);
  return el;
}

/* The two faces of the 24K tile. */
function k24Face(r, cut) {
  const per = UNIT === 1 ? "per gram" : "per 10 g";
  if (cut && r.buy24) {
    return `<span class="kt">Buyback · ${cut}% cut</span>
            <div class="amt">${money(r.buy24 * (1 - cut / 100))}</div>
            <span class="per">${per}</span>`;
  }
  return `<span class="kt">24K ${r.derived24
            ? '<span class="drv" title="Derived: 22K x 24/22">DERIVED</span>' : ""}</span>
          ${r.buy24 ? `<div class="amt">${money(r.buy24)}</div>` : '<div class="none">—</div>'}
          <span class="per">${per}</span>`;
}

/* Turn the tile over, and change the face while its back is to you. */
function flipK24(el, r, cut) {
  const tile = el.querySelector(".rate.k24");
  tile.classList.add("flip");
  setTimeout(() => {
    tile.innerHTML = k24Face(r, cut);
    tile.classList.toggle("buyback", !!cut && !!r.buy24);
  }, 185);
  setTimeout(() => tile.classList.remove("flip"), 420);
}

/* A 24-point trace of where this merchant's 24K rate has been. No axes, no
   labels — it is there to answer "is this one drifting?" at a glance. */
function spark(vals) {
  const w = 240, h = 24, lo = Math.min(...vals), hi = Math.max(...vals);
  const span = (hi - lo) || 1;
  const pts = vals.map((v, i) =>
    `${(i / (vals.length - 1) * w).toFixed(1)},${(h - 2 - ((v - lo) / span) * (h - 5)).toFixed(1)}`);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <polyline points="${pts.join(" ")}" fill="none" stroke="currentColor"
                stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" opacity=".85"/>
    </svg>`;
}

/* ---- manual entry ---- */
function openManual(m) {
  manualFor = m;
  $("mTitle").textContent = m.name;
  $("mSub").textContent = m.adapter === "link_only"
    ? "This site does not hand its rate over. Open it, read the numbers, drop them in here — the board keeps them until you clear them."
    : "A hand-keyed rate overrides whatever was read from the site.";
  // Sites quote in whatever unit they like - Aspect prints per 10 g, Tanishq per
  // gram - so the fields speak in whatever unit the board is currently showing.
  const per = UNIT === 1 ? "/ g" : "/ 10 g";
  document.querySelector('label[for="m22"]').textContent = "22K buy " + per;
  document.querySelector('label[for="m24"]').textContent = "24K buy " + per;
  document.querySelector('label[for="s22"]').textContent = "22K sell " + per;
  document.querySelector('label[for="s24"]').textContent = "24K sell " + per;

  const r = (m.rate && m.rate.manual) ? m.rate : {};
  const show = (v) => v ? Math.round(v * UNIT) : "";
  $("m22").value = show(r.derived22 ? null : r.buy22);
  $("m24").value = show(r.derived24 ? null : r.buy24);
  $("s22").value = show(r.sell22);
  $("s24").value = show(r.sell24);
  $("manualScrim").hidden = false;
  $("m22").focus();
}
$("mCancel").onclick = () => { $("manualScrim").hidden = true; };
$("manualScrim").onclick = (e) => { if (e.target === $("manualScrim")) $("manualScrim").hidden = true; };
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("manualScrim").hidden) $("manualScrim").hidden = true;
});
$("mSave").onclick = async () => {
  const perGram = (v) => {
    const n = parseFloat(String(v).replace(/[^\d.]/g, ""));
    return isFinite(n) && n > 0 ? String(n / UNIT) : "";
  };
  const body = {
    id: manualFor.id, buy22: perGram($("m22").value), buy24: perGram($("m24").value),
    sell22: perGram($("s22").value), sell24: perGram($("s24").value),
  };
  STATE = await (await fetch("/api/manual", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })).json();
  $("manualScrim").hidden = true;
  paint();
  snack("Saved — " + manualFor.short + " is now showing your rate");
};
$("mClear").onclick = async () => {
  STATE = await (await fetch("/api/manual", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: manualFor.id, clear: true }),
  })).json();
  $("manualScrim").hidden = true;
  paint();
  snack("Cleared — back to whatever the site says");
};

/* ---- odds and ends ---- */
function initials(name) {
  return name.replace(/[^A-Za-z ]/g, "").split(/\s+/).filter(Boolean)
    .slice(0, 2).map((w) => w[0].toUpperCase()).join("");
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

load().catch((e) => {
  $("statusText").textContent = "Could not load the rates";
  console.error(e);
});
// Keeps "updated N min ago" honest, and on the hosted page picks up each new
// build within a minute of it landing.
setInterval(() => load().catch(() => {}), 60000);
