/* Parleys UCL — app sin dependencias. */
"use strict";

const TZ = "America/Bogota";
const LS = {
  settings: "pucl.settings",
  slip: "pucl.slip",
  bets: "pucl.bets",
  cache: "pucl.lastData",
};

const state = {
  data: null,
  calib: null,
  preds: null,
  day: null,
  scope: null,
  tier: "seguro",
  detailTab: "mercados",
  open: new Set(),
  running: false,
};

// ---------------------------------------------------------------- utilidades
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (p, d = 0) => (p == null ? "—" : `${(p * 100).toFixed(d)}%`);
const pctSmart = (p) => (p == null ? "—" : p < 0.01 ? `${(p * 100).toFixed(2)}%` : p < 0.1 ? `${(p * 100).toFixed(1)}%` : `${Math.round(p * 100)}%`);
const odds = (o) => (o == null ? "—" : Number(o).toFixed(2));
const money = (n) => new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 }).format(n || 0);
const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* almacenamiento lleno o privado */ } };

function fmtTime(iso) {
  return new Date(iso).toLocaleTimeString("es-CO", { timeZone: TZ, hour: "numeric", minute: "2-digit" });
}
function fmtDay(day) {
  const d = new Date(`${day}T12:00:00-05:00`);
  const s = d.toLocaleDateString("es-CO", { timeZone: TZ, weekday: "short", day: "numeric", month: "short" });
  return s.charAt(0).toUpperCase() + s.slice(1).replace(/\./g, "");
}
function ago(iso) {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "hace un momento";
  if (m < 60) return `hace ${m} min`;
  const h = Math.round(m / 60);
  if (h < 48) return `hace ${h} h`;
  return `hace ${Math.round(h / 24)} días`;
}
function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

function settings() {
  const s = load(LS.settings, {});
  if (!s.repo) {
    const host = location.hostname;
    const seg = location.pathname.split("/").filter(Boolean)[0];
    if (host.endsWith("github.io") && seg) s.repo = `${host.split(".")[0]}/${seg}`;
  }
  return { stake: 10000, weeklyLimit: 0, ...s };
}

// ---------------------------------------------------------------- datos
async function ghRaw(path) {
  const s = settings();
  if (!s.token || !s.repo) return null;
  const r = await fetch(`https://api.github.com/repos/${s.repo}/contents/${path}?ref=main&t=${Date.now()}`, {
    headers: { Authorization: `Bearer ${s.token}`, Accept: "application/vnd.github.raw+json" },
    cache: "no-store",
  });
  if (!r.ok) return null;
  return r.json();
}

async function getJSON(path) {
  try {
    const fromApi = await ghRaw(path);
    if (fromApi) return fromApi;
  } catch { /* sin conexión o token inválido: seguimos con la copia publicada */ }
  try {
    const r = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
    if (r.ok) return r.json();
  } catch { /* sin conexión */ }
  return null;
}

async function loadData() {
  const [data, calib, preds] = await Promise.all([
    getJSON("data/latest.json"),
    getJSON("data/calibration.json"),
    getJSON("data/history/predictions.json"),
  ]);
  if (data) {
    state.data = data;
    try { localStorage.setItem(LS.cache, JSON.stringify(data)); } catch { /* muy grande */ }
  } else {
    state.data = load(LS.cache, null);
  }
  state.calib = calib;
  state.preds = preds;
  if (state.data) {
    const days = state.data.days || [];
    if (!days.includes(state.day)) state.day = days[0] || null;
    if (!state.scope) state.scope = state.day;
  }
  resolveBets();
  renderChrome();
  route();
}

function renderChrome() {
  const d = state.data;
  $("#updated").textContent = d ? `Actualizado ${ago(d.generated_at)}` : "Sin datos todavía";
  const banner = $("#banner");
  if (d && d.demo) {
    banner.hidden = false;
    banner.textContent = "Datos de ejemplo: jugadores, bajas y cuotas son ficticios. Toca Actualizar para cargar la jornada real.";
  } else {
    banner.hidden = true;
  }
  const n = load(LS.slip, []).length;
  const b = $("#slip-count");
  b.hidden = !n;
  b.textContent = n;
}

const matchById = (id) => state.data?.matches.find((m) => String(m.id) === String(id));

// ---------------------------------------------------------------- actualizar (GitHub Actions)
async function triggerUpdate() {
  const s = settings();
  if (!s.token || !s.repo) {
    toast("Primero conecta tu GitHub en Modelo → Ajustes");
    location.hash = "#/modelo";
    return;
  }
  if (state.running) return;
  const btn = $("#refresh");
  const headers = { Authorization: `Bearer ${s.token}`, Accept: "application/vnd.github+json" };
  const base = `https://api.github.com/repos/${s.repo}/actions/workflows/update.yml`;
  state.running = true;
  btn.disabled = true;
  btn.classList.add("running");
  btn.textContent = "Iniciando…";
  const started = Date.now();
  try {
    const r = await fetch(`${base}/dispatches`, { method: "POST", headers, body: JSON.stringify({ ref: "main" }) });
    if (r.status !== 204) {
      const msg = r.status === 401 || r.status === 403 ? "El token no tiene permiso. Revísalo en Ajustes." : `GitHub respondió ${r.status}`;
      throw new Error(msg);
    }
    let run = null;
    for (let i = 0; i < 90; i++) {
      await new Promise((res) => setTimeout(res, i < 3 ? 4000 : 8000));
      const q = await fetch(`${base}/runs?per_page=1&event=workflow_dispatch`, { headers, cache: "no-store" });
      const j = await q.json();
      run = j.workflow_runs?.[0];
      if (run && new Date(run.created_at).getTime() >= started - 60000) {
        const secs = Math.round((Date.now() - started) / 1000);
        btn.textContent = run.status === "completed" ? "Cargando…" : `Analizando ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
        if (run.status === "completed") break;
      }
    }
    if (!run || run.status !== "completed") throw new Error("El análisis sigue corriendo. Vuelve a abrir la app en unos minutos.");
    if (run.conclusion !== "success") throw new Error("El análisis falló. Revisa la pestaña Actions del repositorio.");
    await loadData();
    toast("Jornada actualizada");
  } catch (e) {
    toast(e.message || "No se pudo actualizar", 4500);
  } finally {
    state.running = false;
    btn.disabled = false;
    btn.classList.remove("running");
    btn.textContent = "Actualizar";
  }
}

// ---------------------------------------------------------------- router
function route() {
  const [, view = "jornada", arg] = location.hash.split("/");
  document.querySelectorAll(".tabbar a").forEach((a) => a.classList.toggle("active", a.dataset.tab === (view === "partido" ? "jornada" : view)));
  const el = $("#view");
  const views = { jornada: viewJornada, partido: () => viewPartido(arg), parleys: viewParleys, armar: viewArmar, apuestas: viewApuestas, modelo: viewModelo };
  el.innerHTML = (views[view] || viewJornada)();
  if (route._last !== location.hash) window.scrollTo(0, 0);
  route._last = location.hash;
}

function noData() {
  return `<div class="empty"><h2>Aún no hay análisis</h2><p>Toca <b>Actualizar</b> para correr el modelo con la próxima jornada. Tarda unos 2 a 4 minutos.</p></div>`;
}

// ---------------------------------------------------------------- Jornada
function viewJornada() {
  const d = state.data;
  if (!d || !d.matches.length) return noData();
  const days = d.days;
  const ms = d.matches.filter((m) => m.day === state.day);
  return `
    <h1>Jornada</h1>
    <div class="chips">${days.map((x) => `<button class="chip ${x === state.day ? "on" : ""}" data-day="${x}">${fmtDay(x)}</button>`).join("")}</div>
    ${ms.map(matchRow).join("") || `<p class="muted">No hay partidos este día.</p>`}`;
}

function matchRow(m) {
  const mk = m.markets;
  const p1 = mk["1"].p, px = mk["X"].p, p2 = mk["2"].p;
  const hl = m.highlights || {};
  const tags = [];
  if (hl.value) tags.push(`<span class="tag value">Valor: ${esc(mk[hl.value].label)}</span>`);
  (m.alerts || []).slice(0, 2).forEach((a) => tags.push(`<span class="tag alert">${esc(a)}</span>`));
  if (m.status !== "NS") tags.push(`<span class="tag">${m.status === "FT" ? "Terminado" : "En juego"} ${m.score.home ?? ""}-${m.score.away ?? ""}</span>`);
  return `
  <a class="match" href="#/partido/${m.id}">
    <div class="match-top"><span>${fmtTime(m.kickoff)}</span><span>Calidad de datos ${m.data_quality.score}/100</span></div>
    <div class="teams">
      <div class="team">${crest(m.home)}<span>${esc(m.home.name)}</span></div><div class="team-p">${pct(p1)}</div>
      <div class="team">${crest(m.away)}<span>${esc(m.away.name)}</span></div><div class="team-p">${pct(p2)}</div>
    </div>
    <div class="tri" aria-hidden="true"><i class="h" style="width:${p1 * 100}%"></i><i class="d" style="width:${px * 100}%"></i><i class="a" style="width:${p2 * 100}%"></i></div>
    <div class="tri-legend"><span>Local ${pct(p1)}</span><span>Empate ${pct(px)}</span><span>Visita ${pct(p2)}</span></div>
    ${tags.length ? `<div class="tags">${tags.join("")}</div>` : ""}
  </a>`;
}

const crest = (t) => (t.logo ? `<img src="${esc(t.logo)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : "");

// ---------------------------------------------------------------- Detalle de partido
function viewPartido(id) {
  const m = matchById(id);
  if (!m) return `<a class="back" href="#/jornada">‹ Jornada</a>${noData()}`;
  const tab = state.detailTab;
  const body = tab === "equipos" ? teamsTab(m) : tab === "contexto" ? contextTab(m) : marketsTab(m);
  return `
    <a class="back" href="#/jornada">‹ Jornada</a>
    <div class="scoreboard">
      <div>${m.home.logo ? `<img src="${esc(m.home.logo)}" alt="" onerror="this.style.visibility='hidden'">` : ""}<div class="name">${esc(m.home.name)}</div></div>
      <div class="xg">${m.xg_home.toFixed(1)} – ${m.xg_away.toFixed(1)}<small>goles esperados</small></div>
      <div>${m.away.logo ? `<img src="${esc(m.away.logo)}" alt="" onerror="this.style.visibility='hidden'">` : ""}<div class="name">${esc(m.away.name)}</div></div>
    </div>
    <p class="muted" style="text-align:center;font-size:14px">${fmtDay(m.day)}, ${fmtTime(m.kickoff)}${m.venue ? ` en ${esc(m.venue)}` : ""}</p>
    <div class="quality"><span>Calidad de datos</span><div class="meter"><i style="width:${m.data_quality.score}%"></i></div><span>${m.data_quality.score}/100</span></div>
    <div class="segment" role="tablist">
      ${[["mercados", "Mercados"], ["equipos", "Equipos"], ["contexto", "Por qué"]].map(([k, l]) => `<button role="tab" class="${tab === k ? "on" : ""}" data-dtab="${k}">${l}</button>`).join("")}
    </div>
    ${body}`;
}

function marketsTab(m) {
  const groups = state.data.market_groups;
  return Object.entries(groups).map(([g, keys]) => `
    <h2>${esc(g)}</h2>
    ${keys.map((k) => marketRow(m, k)).join("")}`).join("");
}

function marketRow(m, k) {
  const mk = m.markets[k];
  if (!mk) return "";
  const id = `${m.id}:${k}`;
  const open = state.open.has(id);
  const isValue = mk.ev != null && mk.ev > 0.03;
  const inSlip = load(LS.slip, []).some((l) => l.id === id);
  return `
  <div class="mk ${isValue ? "is-value" : ""}">
    <button data-toggle="${id}" aria-expanded="${open}">
      <span class="mk-label">${esc(mk.label)}</span><span class="mk-p">${pct(mk.p)}</span>
      <span class="bar"><i style="width:${mk.p * 100}%"></i></span>
      <span class="mk-sub">
        <span>Cuota ${mk.odds ? odds(mk.odds) : "sin dato"}</span>
        <span>Justa ${odds(mk.fair_odds)}</span>
        ${isValue ? `<span style="color:var(--amber)">Valor +${Math.round(mk.ev * 100)}%</span>` : ""}
      </span>
    </button>
    ${open ? `<div class="mk-more">
      <p>${esc(mk.explanation)}</p>
      <p class="muted">Modelo ${pct(mk.p_model, 1)}${mk.p_market != null ? `, casa ${pct(mk.p_market, 1)}` : ""}. Probabilidad final ${pct(mk.p, 1)}.</p>
      <div class="row"><button class="btn small ${inSlip ? "" : "primary"}" data-add="${id}">${inSlip ? "Quitar del parley" : "Agregar al parley"}</button></div>
    </div>` : ""}
  </div>`;
}

function teamsTab(m) {
  return [m.home, m.away].map((t) => {
    const f = t.form || {};
    const abs = t.absences || [];
    const xi = t.probable_xi || [];
    return `
    <h2>${esc(t.name)}</h2>
    <div class="panel">
      <p>Elo ${t.elo ? Math.round(t.elo) : "sin dato"}${t.rotation_risk >= 0.25 ? `. <span class="warn">Riesgo de rotación ${pct(t.rotation_risk)}${t.rotation_reason ? `: ${esc(t.rotation_reason)}` : ""}</span>` : ""}</p>
      ${f.n ? `<div style="display:flex;align-items:center;gap:10px;margin-top:8px"><span class="muted" style="font-size:13px">Últimos ${f.n}</span><div class="form">${f.string.split("").map((c) => `<i class="${c}">${c}</i>`).join("")}</div></div>` : ""}
      ${(t.adjust?.reasons || []).length ? `<p class="muted" style="font-size:13.5px;margin-top:8px">Ajustes del modelo: ${esc(t.adjust.reasons.join("; "))} (ataque ×${t.adjust.attack}, defensa ×${t.adjust.defense})</p>` : ""}
    </div>
    <h3 style="margin-top:14px">Bajas y dudas</h3>
    ${abs.length ? abs.map((a) => `
      <div class="player"><div>${esc(a.name)}<small>${esc(a.importance_es)}${a.position ? `, ${esc(a.position)}` : ""}${a.reason ? `. ${esc(a.reason)}` : ""}${a.source ? ` (${esc(a.source)})` : ""}</small></div>
      <div class="st ${a.status}">${esc(a.status_es)}${a.confidence ? `<br><span class="muted">${pct(a.confidence)} seguro</span>` : ""}</div></div>`).join("") : `<p class="muted">Sin bajas reportadas${m.deep ? "" : " todavía. Las noticias se revisan el día antes del partido."}</p>`}
    <h3 style="margin-top:16px">${t.confirmed_lineup ? "Alineación confirmada" : "Once probable"}</h3>
    ${xi.length ? `<div class="xi">${xi.map((p) => `<span>${esc(p.name)} <span class="muted" style="font-size:13px">${esc(p.position || "")}</span>${(p.notes || []).length ? `<br><small class="warn">${esc(p.notes[0])}</small>` : ""}</span><span class="pp">${pct(p.p_start)}</span>`).join("")}</div>` : `<p class="muted">Se calcula el día antes del partido.</p>`}
    ${(t.notes || []).length ? `<h3 style="margin-top:16px">Notas de prensa</h3><ul class="reasons">${t.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    ${(t.headlines || []).length ? `<h3 style="margin-top:16px">Titulares revisados</h3><div class="news">${t.headlines.map((h) => `<a href="${esc(h.url)}" target="_blank" rel="noopener">${esc(h.title)}<small>${esc(h.source)}, hace ${Math.round(h.hours_ago)} h</small></a>`).join("")}</div>` : ""}`;
  }).join("");
}

function contextTab(m) {
  const h2h = m.h2h || {};
  return `
    <h2>Por qué el modelo ve esto</h2>
    <ul class="reasons">${m.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    <h2>Marcadores más probables</h2>
    <div class="scores">${m.top_scores.map((s) => `<div><b>${esc(s.score)}</b><span>${pct(s.p, 1)}</span></div>`).join("")}</div>
    ${h2h.n ? `<h2>Historial entre ambos</h2><p class="muted">${esc(h2h.summary)}</p>${h2h.games.map((g) => `<p style="font-size:14.5px">${esc(g.date)}: ${esc(g.score)}</p>`).join("")}` : ""}
    <h2>Con qué datos se calculó</h2>
    <ul class="reasons">${m.data_quality.items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
}

// ---------------------------------------------------------------- Parleys
function viewParleys() {
  const d = state.data;
  if (!d) return noData();
  const P = d.parlays || {};
  const scopes = [...Object.keys(P.by_day || {})];
  if (P.all && Object.keys(P.all).length) scopes.push("all");
  if (!scopes.includes(state.scope)) state.scope = scopes[0];
  const set = state.scope === "all" ? P.all : (P.by_day || {})[state.scope];
  const list = (set && set[state.tier]) || [];
  const meta = d.tiers[state.tier];
  return `
    <h1>Parleys</h1>
    <div class="chips">${scopes.map((s) => `<button class="chip ${s === state.scope ? "on" : ""}" data-scope="${s}">${s === "all" ? "Toda la jornada" : fmtDay(s)}</button>`).join("")}</div>
    <div class="segment">${Object.entries(d.tiers).map(([k, v]) => `<button class="${k === state.tier ? "on" : ""}" data-tier="${k}">${esc(v.label)}</button>`).join("")}</div>
    <p class="tier-desc">${esc(meta.desc)}</p>
    ${list.length ? list.map((p) => slipCard(p)).join("") : `<div class="empty"><p>No hay suficientes partidos con selecciones de este nivel para este día.</p></div>`}`;
}

function slipCard(p, opts = {}) {
  const s = settings();
  const ret = Math.round(s.stake * p.odds);
  return `
  <article class="slip ${p.tier}">
    <div class="slip-head">
      <div class="slip-p">${pctSmart(p.p)}<small>probabilidad de acertarlo todo</small></div>
      <div class="slip-odds"><b>${odds(p.odds)}</b><span>cuota${p.odds_estimated ? " aprox." : ""}</span></div>
    </div>
    <div class="slip-legs">
      ${p.legs.map((l) => {
        const id = `leg:${p.id}:${l.match_id}:${l.key}`;
        const open = state.open.has(id);
        const cls = l.hit === true ? "hit" : l.hit === false ? "miss" : "";
        return `<div class="leg ${cls}"><button data-toggle="${id}" aria-expanded="${open}">
          <span class="leg-match">${esc(l.match)}, ${fmtTime(l.kickoff)}</span>
          <span class="leg-label">${esc(l.label)}</span>
          <span class="leg-p">${pct(l.p)}<small>@ ${odds(l.odds)}${l.odds_estimated ? "*" : ""}</small></span>
        </button>${open ? `<div class="leg-why">${esc(l.explanation || "")}</div>` : ""}</div>`;
      }).join("")}
    </div>
    ${opts.readonly ? "" : `<div class="slip-foot">
      <span class="slip-note">Con ${money(s.stake)} recibirías ${money(ret)}.${p.odds_estimated ? " *Cuota estimada: confírmala en tu casa de apuestas." : ""}</span>
      <button class="btn primary small" data-register="${p.id}">Registrar apuesta</button>
      <button class="btn small" data-toslip="${p.id}">Editar en Armar</button>
      <button class="btn small" data-share="${p.id}">Compartir</button>
    </div>`}
  </article>`;
}

function findParlay(pid) {
  const P = state.data?.parlays || {};
  const all = [...Object.values(P.by_day || {}), P.all || {}].flatMap((t) => Object.values(t).flat());
  return all.find((p) => p.id === pid);
}

// ---------------------------------------------------------------- Armar
function legFromMarket(m, key) {
  const mk = m.markets[key];
  const est = !mk.odds;
  return {
    id: `${m.id}:${key}`, match_id: m.id, key, label: mk.label, match: `${m.home.name} vs ${m.away.name}`,
    kickoff: m.kickoff, p: mk.p, odds: mk.odds || Math.max(1.01, +(1 / (mk.p * 1.07)).toFixed(2)),
    odds_estimated: est, explanation: mk.explanation,
  };
}

function viewArmar() {
  const slip = load(LS.slip, []);
  if (!slip.length) {
    return `<h1>Armar parley</h1><div class="empty"><h2>Tu boleta está vacía</h2><p>Abre un partido en Jornada, toca un mercado y elige <b>Agregar al parley</b>. Aquí verás la probabilidad real de la combinación.</p><a class="btn primary" href="#/jornada" style="display:inline-block;text-decoration:none;margin-top:10px">Ir a la jornada</a></div>`;
  }
  // Actualiza probabilidades con los datos más recientes
  const legs = slip.map((l) => {
    const m = matchById(l.match_id);
    return m && m.markets[l.key] ? legFromMarket(m, l.key) : l;
  });
  const byMatch = {};
  legs.forEach((l) => { byMatch[l.match_id] = (byMatch[l.match_id] || 0) + 1; });
  const dup = Object.values(byMatch).some((n) => n > 1);
  const p = legs.reduce((a, l) => a * l.p, 1);
  const o = legs.reduce((a, l) => a * l.odds, 1);
  const parlay = { id: "custom", tier: "custom", legs, p, odds: +o.toFixed(2), odds_estimated: legs.some((l) => l.odds_estimated) };
  return `
    <h1>Armar parley</h1>
    ${dup ? `<div class="banner" style="margin:10px 0">Tienes dos selecciones del mismo partido. Esas no son independientes y la probabilidad de abajo sale mal. Usa los mercados de <b>Combinadas del partido</b>, que ya vienen calculados en conjunto.</div>` : ""}
    ${slipCard(parlay, { readonly: true })}
    <div class="panel">
      <label class="field"><span>Monto a apostar (COP)</span><input id="stake" type="number" inputmode="numeric" min="0" step="1000" value="${settings().stake}"></label>
      <p>Si aciertas recibes <b id="ret">${money(settings().stake * o)}</b>. Valor esperado ${pct(p * o - 1, 1)}.</p>
      <div class="row" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
        <button class="btn primary" data-register="custom">Registrar apuesta</button>
        ${legs.map((l) => `<button class="btn small ghost" data-remove="${l.id}">Quitar ${esc(l.label)}</button>`).join("")}
        <button class="btn small ghost" data-clear="1">Vaciar boleta</button>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- Mis apuestas
function resolveBets() {
  const bets = load(LS.bets, []);
  const preds = state.preds || {};
  const results = {};
  Object.entries(preds).forEach(([id, p]) => { if (p.result) results[id] = p.result; });
  (state.data?.matches || []).forEach((m) => { if (m.status === "FT" && m.score.home != null) results[m.id] = [m.score.home, m.score.away]; });
  let changed = false;
  bets.forEach((b) => {
    if (b.status !== "pending") return;
    let all = true;
    let lost = false;
    b.legs.forEach((l) => {
      const r = results[l.match_id];
      if (!r) { all = false; return; }
      l.hit = outcome(l.key, r[0], r[1]);
      if (!l.hit) lost = true;
    });
    if (lost) { b.status = "lost"; changed = true; } else if (all) { b.status = "won"; changed = true; }
  });
  if (changed) save(LS.bets, bets);
}

function outcome(key, gh, ga) {
  const tot = gh + ga, res = gh > ga ? "1" : gh === ga ? "X" : "2", btts = gh > 0 && ga > 0;
  const m = {
    "1": res === "1", "X": res === "X", "2": res === "2", "1X": res !== "2", "X2": res !== "1", "12": res !== "X",
    btts_yes: btts, btts_no: !btts, "home_over_0.5": gh >= 1, "away_over_0.5": ga >= 1, "home_over_1.5": gh >= 2, "away_over_1.5": ga >= 2,
    "1_over_1.5": res === "1" && tot >= 2, "2_over_1.5": res === "2" && tot >= 2, "1_btts_yes": res === "1" && btts, "2_btts_yes": res === "2" && btts,
    "1X_under_3.5": res !== "2" && tot <= 3, "X2_under_3.5": res !== "1" && tot <= 3, "1X_btts_yes": res !== "2" && btts, "X2_btts_yes": res !== "1" && btts,
  };
  if (key in m) return m[key];
  if (key.startsWith("over_")) return tot > parseFloat(key.slice(5));
  if (key.startsWith("under_")) return tot < parseFloat(key.slice(6));
  return false;
}

function weekStart() {
  const d = new Date();
  const day = (d.getDay() + 6) % 7;
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - day);
  return d.getTime();
}

function viewApuestas() {
  const bets = load(LS.bets, []).sort((a, b) => b.t - a.t);
  const s = settings();
  const settled = bets.filter((b) => b.status !== "pending");
  const staked = settled.reduce((a, b) => a + b.stake, 0);
  const returned = settled.reduce((a, b) => a + (b.status === "won" ? b.stake * b.odds : 0), 0);
  const won = settled.filter((b) => b.status === "won").length;
  const expected = settled.reduce((a, b) => a + b.p, 0);
  const week = bets.filter((b) => b.t >= weekStart()).reduce((a, b) => a + b.stake, 0);
  const tiers = {};
  settled.forEach((b) => {
    const t = (tiers[b.tier] ||= { n: 0, w: 0, net: 0 });
    t.n++; if (b.status === "won") t.w++;
    t.net += b.status === "won" ? b.stake * (b.odds - 1) : -b.stake;
  });
  const names = { seguro: "Seguro", equilibrado: "Equilibrado", sonador: "Soñador", custom: "Armado por mí" };
  return `
    <h1>Mis apuestas</h1>
    ${s.weeklyLimit ? `<div class="panel"><p>Esta semana llevas <b>${money(week)}</b> de tu límite de ${money(s.weeklyLimit)}.</p><div class="meter" style="margin-top:8px"><i style="width:${Math.min(100, (week / s.weeklyLimit) * 100)}%;background:${week > s.weeklyLimit ? "var(--card)" : "var(--pitch)"}"></i></div>${week > s.weeklyLimit ? `<p class="warn" style="margin-top:8px">Pasaste el límite que te pusiste.</p>` : ""}</div>` : ""}
    <div class="panel stats">
      <div class="stat"><b>${money(staked)}</b><span>apostado (resueltas)</span></div>
      <div class="stat"><b class="${returned - staked >= 0 ? "pos" : "neg"}">${money(returned - staked)}</b><span>balance</span></div>
      <div class="stat"><b>${settled.length ? `${won}/${settled.length}` : "—"}</b><span>acertadas (el modelo esperaba ${expected.toFixed(1)})</span></div>
      <div class="stat"><b>${staked ? pct((returned - staked) / staked, 1) : "—"}</b><span>retorno sobre lo apostado</span></div>
    </div>
    ${Object.keys(tiers).length ? `<table class="table"><tr><th>Tipo</th><th class="r">Acertadas</th><th class="r">Balance</th></tr>${Object.entries(tiers).map(([k, t]) => `<tr><td>${names[k] || k}</td><td class="r">${t.w}/${t.n}</td><td class="r ${t.net >= 0 ? "pos" : "neg"}">${money(t.net)}</td></tr>`).join("")}</table>` : ""}
    <h2>Historial</h2>
    ${bets.length ? bets.map((b) => `
      <div class="bet">
        <div class="bet-top"><b>${names[b.tier] || "Parley"}, ${b.legs.length} ${b.legs.length === 1 ? "selección" : "selecciones"}</b><span class="${b.status === "won" ? "pos" : b.status === "lost" ? "neg" : "muted"}">${b.status === "won" ? `Ganada ${money(b.stake * b.odds)}` : b.status === "lost" ? "Perdida" : "Pendiente"}</span></div>
        <small>${new Date(b.t).toLocaleDateString("es-CO", { timeZone: TZ, day: "numeric", month: "short" })}: ${money(b.stake)} a cuota ${odds(b.odds)} (prob. ${pctSmart(b.p)})</small>
        <div style="font-size:14px;margin-top:4px">${b.legs.map((l) => `<div>${l.hit === true ? "✓" : l.hit === false ? "✗" : "·"} ${esc(l.label)} <span class="muted">(${esc(l.match)})</span></div>`).join("")}</div>
        ${b.status === "pending" ? `<div style="display:flex;gap:8px;margin-top:8px"><button class="btn small" data-mark="${b.t}:won">Marcar ganada</button><button class="btn small" data-mark="${b.t}:lost">Marcar perdida</button><button class="btn small ghost" data-del="${b.t}">Borrar</button></div>` : ""}
      </div>`).join("") : `<p class="muted">Cuando registres una apuesta aparecerá aquí y se resolverá sola al terminar los partidos.</p>`}
    <div style="display:flex;gap:8px;margin-top:16px;flex-wrap:wrap">
      <button class="btn small" data-export="1">Exportar copia</button>
      <label class="btn small">Importar copia<input type="file" accept="application/json" id="import" hidden></label>
    </div>`;
}

// ---------------------------------------------------------------- Modelo
function viewModelo() {
  const c = state.calib;
  const d = state.data;
  const s = settings();
  const buckets = (c?.buckets || []).filter((b) => b.n);
  const tierNames = { seguro: "Seguro", equilibrado: "Equilibrado", sonador: "Soñador" };
  return `
    <h1>Modelo</h1>
    <h2>¿Se cumplen los porcentajes?</h2>
    ${buckets.length ? `
      <p class="muted" style="font-size:14.5px">Si el modelo es confiable, lo que dice 70% debería pasar cerca de 7 de cada 10 veces. Puntos sobre la diagonal: se cumple.</p>
      ${calibChart(buckets)}
      <table class="table"><tr><th>El modelo dijo</th><th class="r">Pasó</th><th class="r">Casos</th></tr>
      ${buckets.map((b) => `<tr><td>${pct(b.p_avg)}</td><td class="r">${pct(b.hit_rate)}</td><td class="r">${b.n}</td></tr>`).join("")}</table>
      <p class="muted" style="font-size:13px">${c.matches_graded} partidos calificados. Puntaje Brier ${c.brier} (más bajo es mejor; 0.25 equivale a tirar una moneda).</p>`
      : `<p class="muted">Todavía no hay partidos calificados. Después de la primera jornada real verás aquí si los porcentajes se cumplen.</p>`}
    ${c && Object.keys(c.parlay_tiers || {}).length ? `
      <h2>Parleys sugeridos que ya se jugaron</h2>
      <table class="table"><tr><th>Nivel</th><th class="r">Acertados</th><th class="r">Esperados</th><th class="r">Unidades</th></tr>
      ${Object.entries(c.parlay_tiers).map(([k, t]) => `<tr><td>${tierNames[k] || k}</td><td class="r">${t.hits}/${t.n}</td><td class="r">${t.expected_hits}</td><td class="r ${t.units >= 0 ? "pos" : "neg"}">${t.units > 0 ? "+" : ""}${t.units}</td></tr>`).join("")}</table>
      <p class="muted" style="font-size:13px">Unidades: lo que habrías ganado o perdido apostando 1 unidad a cada parley sugerido.</p>` : ""}
    ${d ? `<h2>Última actualización</h2>
      <div class="panel" style="font-size:14.5px">
        <p>${new Date(d.generated_at).toLocaleString("es-CO", { timeZone: TZ })}, en ${d.run.seconds} s.</p>
        <p>API-Football: ${d.run.api_requests} de ${d.run.api_budget} peticiones permitidas por corrida (100 al día en el plan gratis).</p>
        <p>IA que leyó noticias: Groq ${d.run.llm.groq}, Gemini ${d.run.llm.gemini}${d.run.llm.failed ? `, sin respuesta ${d.run.llm.failed}` : ""}.</p>
        ${Object.entries(d.run.sources || {}).filter(([, v]) => v.errors).map(([k, v]) => `<p class="warn">${esc(k)}: ${v.errors} errores. ${esc(v.last_error)}</p>`).join("")}
      </div>` : ""}
    <h2>Ajustes</h2>
    <div class="panel">
      <label class="field"><span>Repositorio de GitHub</span><input id="set-repo" value="${esc(s.repo || "")}" placeholder="usuario/champions-predictor" autocapitalize="off" autocorrect="off"></label>
      <label class="field"><span>Token de GitHub (para el botón Actualizar)</span><input id="set-token" type="password" value="${esc(s.token || "")}" placeholder="github_pat_…" autocapitalize="off" autocorrect="off"></label>
      <label class="field"><span>Monto por defecto (COP)</span><input id="set-stake" type="number" inputmode="numeric" step="1000" value="${s.stake}"></label>
      <label class="field"><span>Límite semanal de apuestas (COP, 0 = sin límite)</span><input id="set-limit" type="number" inputmode="numeric" step="10000" value="${s.weeklyLimit}"></label>
      <button class="btn primary" data-save-settings="1">Guardar ajustes</button>
      <p class="muted" style="font-size:13px;margin-top:10px">El token se guarda solo en este teléfono.</p>
    </div>
    <details class="help"><summary>Cómo crear el token</summary><ol>
      <li>En GitHub: Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token.</li>
      <li>Repository access: solo <b>champions-predictor</b>.</li>
      <li>Permissions: <b>Actions</b> lectura y escritura, <b>Contents</b> solo lectura.</li>
      <li>Copia el token y pégalo arriba.</li></ol></details>
    <details class="help"><summary>Instalar en el iPhone</summary><ol>
      <li>Abre esta página en Safari.</li><li>Toca el botón Compartir.</li><li>Elige <b>Agregar a inicio</b>.</li></ol></details>
    <p class="muted" style="font-size:13px;margin-top:18px">Los porcentajes son estimaciones de un modelo y pueden fallar. Ningún sistema garantiza ganancias. Apuesta solo lo que estés dispuesto a perder.</p>`;
}

function calibChart(buckets) {
  const W = 300, H = 200, pad = 28;
  const x = (v) => pad + v * (W - pad - 8);
  const y = (v) => H - pad - v * (H - pad - 8);
  const pts = buckets.map((b) => `<circle cx="${x(b.p_avg)}" cy="${y(b.hit_rate)}" r="${Math.min(10, 3 + Math.sqrt(b.n))}" fill="var(--amber)" fill-opacity="0.85"/>`).join("");
  const ticks = [0, 0.5, 1].map((t) => `<text x="${x(t)}" y="${H - 8}" fill="var(--mist)" font-size="11" text-anchor="middle">${t * 100}%</text><text x="${pad - 6}" y="${y(t) + 4}" fill="var(--mist)" font-size="11" text-anchor="end">${t * 100}%</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:420px;display:block;margin:8px 0" role="img" aria-label="Probabilidad dicha contra frecuencia real">
    <line x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}" stroke="var(--ink-3)" stroke-width="2" stroke-dasharray="4 4"/>
    <line x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(0)}" stroke="var(--ink-3)"/><line x1="${x(0)}" y1="${y(0)}" x2="${x(0)}" y2="${y(1)}" stroke="var(--ink-3)"/>
    ${ticks}${pts}</svg>`;
}

// ---------------------------------------------------------------- acciones
function registerBet(parlay) {
  const s = settings();
  const stakeInput = $("#stake");
  const stake = stakeInput ? Math.max(0, +stakeInput.value || 0) : s.stake;
  if (!stake) return toast("Escribe un monto");
  const bets = load(LS.bets, []);
  const week = bets.filter((b) => b.t >= weekStart()).reduce((a, b) => a + b.stake, 0);
  if (s.weeklyLimit && week + stake > s.weeklyLimit && !confirm(`Con esta apuesta pasas tu límite semanal de ${money(s.weeklyLimit)}. ¿Registrarla igual?`)) return;
  bets.push({
    t: Date.now(), tier: parlay.tier, stake, odds: parlay.odds, p: parlay.p, status: "pending",
    legs: parlay.legs.map((l) => ({ match_id: l.match_id, key: l.key, label: l.label, match: l.match })),
  });
  save(LS.bets, bets);
  toast("Apuesta registrada en Mis apuestas");
}

document.addEventListener("click", async (e) => {
  const t = e.target.closest("button, [data-day]");
  if (!t) return;
  const ds = t.dataset;
  if (ds.day) { state.day = ds.day; route(); }
  else if (ds.scope) { state.scope = ds.scope; route(); }
  else if (ds.tier) { state.tier = ds.tier; route(); }
  else if (ds.dtab) { state.detailTab = ds.dtab; route(); }
  else if (ds.toggle) { state.open.has(ds.toggle) ? state.open.delete(ds.toggle) : state.open.add(ds.toggle); route(); }
  else if (ds.add) {
    const [mid, key] = ds.add.split(":");
    let slip = load(LS.slip, []);
    if (slip.some((l) => l.id === ds.add)) { slip = slip.filter((l) => l.id !== ds.add); toast("Quitado del parley"); }
    else { slip.push(legFromMarket(matchById(mid), key)); toast("Agregado al parley"); }
    save(LS.slip, slip); renderChrome(); route();
  }
  else if (ds.remove) { save(LS.slip, load(LS.slip, []).filter((l) => l.id !== ds.remove)); renderChrome(); route(); }
  else if (ds.clear) { save(LS.slip, []); renderChrome(); route(); }
  else if (ds.register) {
    if (ds.register === "custom") {
      const legs = load(LS.slip, []).map((l) => { const m = matchById(l.match_id); return m ? legFromMarket(m, l.key) : l; });
      registerBet({ tier: "custom", legs, p: legs.reduce((a, l) => a * l.p, 1), odds: +legs.reduce((a, l) => a * l.odds, 1).toFixed(2) });
    } else { registerBet(findParlay(ds.register)); }
  }
  else if (ds.toslip) {
    const p = findParlay(ds.toslip);
    save(LS.slip, p.legs.map((l) => { const m = matchById(l.match_id); return m ? legFromMarket(m, l.key) : { ...l, id: `${l.match_id}:${l.key}` }; }));
    renderChrome(); location.hash = "#/armar";
  }
  else if (ds.share) {
    const p = findParlay(ds.share);
    const text = `Parley ${p.tier_label} (${pctSmart(p.p)}, cuota ${odds(p.odds)})\n` + p.legs.map((l) => `• ${l.label} — ${l.match} (${pct(l.p)})`).join("\n");
    if (navigator.share) { try { await navigator.share({ text }); } catch { /* cancelado */ } }
    else { await navigator.clipboard?.writeText(text); toast("Copiado"); }
  }
  else if (ds.mark) {
    const [ts, st] = ds.mark.split(":");
    const bets = load(LS.bets, []);
    const b = bets.find((x) => String(x.t) === ts); if (b) b.status = st;
    save(LS.bets, bets); route();
  }
  else if (ds.del) { save(LS.bets, load(LS.bets, []).filter((x) => String(x.t) !== ds.del)); route(); }
  else if (ds.export) {
    const blob = new Blob([JSON.stringify({ bets: load(LS.bets, []) }, null, 1)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "mis-apuestas.json"; a.click();
  }
  else if (ds.saveSettings) {
    const cur = load(LS.settings, {});
    save(LS.settings, {
      ...cur, repo: $("#set-repo").value.trim(), token: $("#set-token").value.trim(),
      stake: Math.max(0, +$("#set-stake").value || 0), weeklyLimit: Math.max(0, +$("#set-limit").value || 0),
    });
    toast("Ajustes guardados"); loadData();
  }
});

document.addEventListener("input", (e) => {
  if (e.target.id === "stake") {
    const legs = load(LS.slip, []);
    const o = legs.reduce((a, l) => a * l.odds, 1);
    const r = $("#ret"); if (r) r.textContent = money((+e.target.value || 0) * o);
  }
});

document.addEventListener("change", async (e) => {
  if (e.target.id !== "import" || !e.target.files[0]) return;
  try {
    const j = JSON.parse(await e.target.files[0].text());
    if (!Array.isArray(j.bets)) throw new Error();
    const cur = load(LS.bets, []);
    const seen = new Set(cur.map((b) => b.t));
    save(LS.bets, cur.concat(j.bets.filter((b) => !seen.has(b.t))));
    toast("Apuestas importadas"); route();
  } catch { toast("Ese archivo no es una copia válida"); }
});

$("#refresh").addEventListener("click", triggerUpdate);
window.addEventListener("hashchange", route);
document.addEventListener("visibilitychange", () => { if (!document.hidden && state.data && !state.running) renderChrome(); });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
if (!location.hash) location.hash = "#/jornada";
state.data = load(LS.cache, null);
if (state.data) { state.day = state.data.days?.[0]; state.scope = state.day; renderChrome(); route(); }
loadData();
