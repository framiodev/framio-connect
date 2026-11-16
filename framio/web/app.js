"use strict";
/* Framio Connect UI */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let S = null, STR = {}, LOGS = [], page = "share", viewerId = null, uploading = false;
const lang = () => (S && S.lang) || "en";
const t = (k, kw) => {
  let s = (STR[lang()] || {})[k] ?? (STR.en || {})[k] ?? k;
  return kw ? s.replace(/\{(\w+)\}/g, (_, a) => kw[a] ?? "") : s;
};
const fmtId = (id) => String(id || "").replace(/(\d{3})(?=\d)/g, "$1 ");
const svg = (id, cls = "ic") => `<svg class="${cls}"><use href="#i-${id}"/></svg>`;

/* ------------------------------------------------------------ boot + events */
async function boot() {
  const r = await fetch("/api/boot", { headers: { "X-Token": TOK } });
  const d = await r.json();
  STR = d.strings; S = d.state; LOGS = d.logs || [];
  render(true);
  [0, 150, 500].forEach((d) => setTimeout(() => { $("#main").scrollTop = 0; }, d));
  const es = new EventSource("/api/events?t=" + encodeURIComponent(TOK));
  es.onmessage = (e) => onEvent(JSON.parse(e.data));
}
function onEvent(m) {
  switch (m.k) {
    case "state": S = m.state; render(); break;
    case "log": LOGS.push(m.item); LOGS = LOGS.slice(-80); renderLog(true); break;
    case "toast": toast(t(m.key, m.kw), m.kind); break;
    case "approve": showApprove(m); break;
    case "overlay": showDropOv(m); break;
    case "overlay_hide": hideDropOv(); break;
    case "viewer_closed": if (m.vid === viewerId) closeViewer(false); break;
  }
}

/* ------------------------------------------------------------ toasts */
function toast(text, kind = "info") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = text;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 380); }, 4200);
}

/* ------------------------------------------------------------ components */
function paintSeg(el, value) {
  const btns = $$("button", el);
  let on = btns.find((b) => b.dataset.v === value) || btns[0];
  btns.forEach((b) => b.classList.toggle("on", b === on));
  const th = $(".thumb", el);
  requestAnimationFrame(() => {
    th.style.width = on.offsetWidth + "px";
    th.style.transform = `translateX(${on.offsetLeft - 4}px)`;
  });
}
function bindSeg(id, onPick) {
  const el = $(id);
  $$("button", el).forEach((b) => b.addEventListener("click", () => onPick(b.dataset.v)));
}
function setToggle(id, on) { $(id).setAttribute("aria-checked", on ? "true" : "false"); }
function bindToggle(id, onSet) {
  const el = $(id);
  el.addEventListener("click", () => {
    const v = el.getAttribute("aria-checked") !== "true";
    el.setAttribute("aria-checked", v ? "true" : "false");
    onSet(v);
  });
}
function setVal(id, v) { const el = $(id); if (document.activeElement !== el && el.value !== v) el.value = v ?? ""; }
async function copyText(txt) {
  try { await navigator.clipboard.writeText(txt); }
  catch { const ta = document.createElement("textarea"); ta.value = txt; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
  toast(t("copied"), "ok");
}

/* ------------------------------------------------------------ render */
function applyI18n() {
  $$("[data-i]").forEach((el) => { el.textContent = t(el.dataset.i); });
  $$("[data-it]").forEach((el) => { el.title = t(el.dataset.it); });
  document.documentElement.lang = lang();
}
function applyTheme() {
  const pref = S.theme;
  const eff = pref === "auto" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : pref;
  if (document.documentElement.dataset.theme !== eff) document.documentElement.dataset.theme = eff;
  if (applyTheme.last !== eff) { applyTheme.last = eff; api("set_titlebar", eff === "dark"); setTimeout(() => api("set_titlebar", eff === "dark"), 1200); }
}
function statusInfo() {
  const m = {
    stopped: ["st_stopped", "var(--muted)", false], waiting: ["st_waiting", "var(--warn)", true],
    connected: ["st_connected", "var(--ok)", true], active: ["st_active", "var(--accent)", true],
    connecting: ["st_connecting", "var(--warn)", true], disconnected: ["st_disconnected", "var(--danger)", false],
  };
  const [k, c, live] = m[S.status] || m.stopped;
  return { text: t(k, { name: S.active || "" }), color: c, live };
}

function render(first) {
  applyTheme();
  applyI18n();
  $("#ver").textContent = "v" + S.version;
  // ---- share
  paintSeg($("#segMode"), S.mode);
  paintSeg($("#segSide"), S.side);
  const host = S.mode === "host";
  $("#fIps").hidden = !host; $("#fHost").hidden = host; $("#fSide").hidden = host;
  $("#btnCopyKey").hidden = !host; $("#btnNewKey").hidden = !host;
  $("#ips").innerHTML = S.ips.length ? S.ips.map((i) => `<span class="chip">${esc(i)}</span>`).join("") : '<span class="empty-note">-</span>';
  setVal("#keyInput", S.key); setVal("#hostInput", S.host);
  setToggle("#tgClipText", S.clip_text); setToggle("#tgClipFiles", S.clip_files);
  const btn = $("#btnShare");
  btn.classList.toggle("stop", S.running); btn.classList.toggle("primary", !S.running);
  $("span", btn).textContent = S.running ? t("stop") : t("start");
  const st = statusInfo(), pill = $("#pill");
  pill.style.setProperty("--c", st.color); pill.classList.toggle("live", st.live);
  $("span", pill).textContent = st.text;
  renderStage();
  renderLog();
  // ---- files
  renderFiles();
  // ---- remote
  $("#myId").textContent = fmtId(S.device.id);
  setVal("#remotePw", S.remote.password);
  setToggle("#tgAllow", S.remote.enabled); setToggle("#tgApproval", S.remote.approval);
  renderSessions(); renderNearby();
  // ---- settings
  paintSeg($("#segLang"), S.lang); paintSeg($("#segTheme"), S.theme);
  setToggle("#tgAutostart", S.autostart); setToggle("#tgAutoUpd", S.auto_update);
  $("#btnAdmin").hidden = S.admin;
  $("#verLine").textContent = t("version") + " " + S.version;
  renderUpdate();
  moveNav();
}

function monitor(opts) {
  const { name, sub, cls = "", cursor = false, delay = 0, key = "" } = opts;
  return `<div class="mon ${cls}" style="animation-delay:${delay}ms"><div class="screen"><div class="wall"></div>${cursor ? svg("cursor", "cursor") : ""}</div><div class="neck"></div><div class="foot"></div><b>${esc(name)}</b><small data-k="${esc(key)}">${esc(sub)}</small></div>`;
}
function renderStage() {
  let h = "";
  if (S.mode === "host") {
    const by = Object.fromEntries(S.clients.map((c) => [c.side, c]));
    const slot = (side, d) => {
      const c = by[side];
      return c ? monitor({ name: c.name, sub: c.ip, key: c.side, cls: S.active === c.name ? "on" : "", cursor: S.active === c.name, delay: d })
               : monitor({ name: t("waiting_pc"), sub: t(side + "_s"), cls: "empty", delay: d });
    };
    const link = (side) => `<div class="link ${by[side] ? "live" : ""}"></div>`;
    h = slot("left", 0) + link("left") + monitor({ name: t("this_pc"), sub: t("host_label"), cls: "me" + (S.running && !S.active ? " on" : ""), delay: 60 }) + link("right") + slot("right", 120);
  } else {
    const ok = S.status === "connected";
    const me = monitor({ name: t("this_pc"), sub: t("role_client"), cls: "me" + (ok ? " on" : ""), delay: 0 });
    const hs = monitor({ name: t("host_label"), sub: S.host || "-", cls: ok ? "" : "empty", delay: 90 });
    const link = `<div class="link ${ok ? "live" : ""}"></div>`;
    h = S.side === "left" ? me + link + hs : hs + link + me;
  }
  const sig = h + S.active + S.status;
  const el = $("#stage");
  if (el._sig !== sig) { el._sig = sig; el.innerHTML = h; }
  if (S.mode === "host") for (const c of S.clients) {          // live latency without re-creating the monitors
    const s = $(`#stage small[data-k="${c.side}"]`);
    if (s) s.textContent = c.ip + (c.rtt != null ? " · " + c.rtt + " ms" : "") + " · " + (c.udp ? "UDP" : "TCP");
  }
}

const LOG_LV = { error: "bad", auth_failed: "bad", auth_rejected: "bad", no_connection: "warn", r_bad_password: "bad", r_denied: "warn", connected: "ok", connected_host: "ok", r_started: "ok", started: "ok" };
function logText(it) {
  const kw = { ...it.kw };
  if (kw.side) kw.side = t(kw.side + "_s");
  return t("log_" + it.code, kw);
}
function renderLog(append) {
  const el = $("#log");
  if (!LOGS.length) { el.innerHTML = `<div class="empty-note">${esc(t("log_empty"))}</div>`; return; }
  el.innerHTML = LOGS.slice().reverse().map((it) => `<div class="ll"><time>${esc(it.t)}</time><i class="lv ${LOG_LV[it.code] || ""}"></i><span>${esc(logText(it))}</span></div>`).join("");
}

/* ---- files */
function targets() {
  if (S.mode === "host") return S.clients.map((c) => ({ key: c.side, name: c.name }));
  if (S.running && S.status === "connected") return [{ key: "host", name: t("host_word") }];
  return [];
}
let target = null;
function renderFiles() {
  const tg = targets();
  if (!tg.find((x) => x.key === target)) target = tg[0] ? tg[0].key : null;
  $("#targets").innerHTML = tg.length ? tg.map((x) => `<button class="tchip ${x.key === target ? "on" : ""}" data-k="${esc(x.key)}">${svg("monitor")}${esc(x.name)}</button>`).join("") : `<span class="empty-note">${esc(t("no_target"))}</span>`;
  $$("#targets .tchip").forEach((b) => b.addEventListener("click", () => { target = b.dataset.k; renderFiles(); }));
  $("#drop").classList.toggle("off", !tg.length);
  const tr = S.transfers.slice().reverse();
  $("#transfers").innerHTML = tr.length ? tr.map((x) => {
    const cls = x.status === "done" ? "state-ok" : x.status === "error" ? "state-bad" : "";
    return `<div class="item"><div class="fi">${svg(x.status === "done" ? "check" : "files")}</div><div class="meta"><b>${esc(x.name)}</b><small class="${cls}">${esc(t("t_" + x.status))}${x.status === "sending" ? " " + x.pct + "%" : ""}</small>${x.status === "sending" ? `<div class="bar"><i style="width:${x.pct}%"></i></div>` : ""}</div></div>`;
  }).join("") : `<div class="empty-note">-</div>`;
  const rc = S.received;
  $("#received").innerHTML = rc.length ? rc.slice(0, 8).map((x, i) => `<div class="item click" data-i="${i}"><div class="fi">${svg("file")}</div><div class="meta"><b>${esc(x.name)}</b><small>${esc(x.t)} · ${esc(x.who === "host" ? t("host_word") : x.who)}</small></div><span class="act btn ghost sm">${svg("folder")}</span></div>`).join("") : `<div class="empty-note">${esc(t("nothing_received"))}</div>`;
  $$("#received .item").forEach((el) => el.addEventListener("click", () => api("reveal", +el.dataset.i)));
}

/* ---- remote */
function renderSessions() {
  const ss = S.remote.sessions;
  $("#sessions").innerHTML = ss.length ? ss.map((x) => `<div class="sess"><i class="vdot"></i><span>${esc(x.name)} <small style="color:var(--muted);font-family:var(--mono)">${esc(x.ip)}</small></span><button class="btn danger sm" data-id="${esc(x.id)}">${esc(t("disconnect"))}</button></div>`).join("") : `<div class="empty-note">${esc(t(S.remote.enabled ? "no_sessions" : "remote_off"))}</div>`;
  $$("#sessions button").forEach((b) => b.addEventListener("click", () => api("disconnect_session", b.dataset.id)));
}
function renderNearby() {
  const ps = S.peers.filter((p) => p.remote);
  const sig = JSON.stringify(ps) + lang();
  const el = $("#nearby");
  if (el._sig === sig) return;
  el._sig = sig;
  el.innerHTML = ps.length ? ps.map((p) => `<button class="dev" data-id="${esc(p.id)}">${svg("monitor")}<span><b>${esc(p.name)}</b><small>${esc(fmtId(p.id))} · ${esc(p.ip)}</small></span></button>`).join("") : `<div class="empty-note">${esc(t("no_nearby"))}</div>`;
  $$("#nearby .dev").forEach((b) => b.addEventListener("click", () => { $("#partnerInput").value = fmtId(b.dataset.id); $("#partnerPw").focus(); }));
}

/* ---- settings / updates */
function renderUpdate() {
  const u = S.update || {};
  const msg = $("#updMsg");
  msg.textContent = u.code ? t(u.code, u.kw) : "";
  msg.classList.toggle("bad", u.code === "update_err");
  const bar = $("#updBar");
  bar.hidden = u.pct == null;
  if (u.pct != null) $("i", bar).style.width = u.pct + "%";
  const chip = $("#updChip");
  const show = ["update_found", "updating"].includes(u.code);
  chip.hidden = !show;
  if (show) $("span", chip).textContent = t(u.code, u.kw);
}

/* ------------------------------------------------------------ navigation */
function moveNav() {
  const b = $(`#nav button[data-page=${page}]`);
  if (!b) return;
  $("#nav .nav-ind").style.transform = `translateY(${b.offsetTop}px)`;
}
function go(p) {
  page = p;
  $$("#nav button").forEach((b) => b.classList.toggle("on", b.dataset.page === p));
  $$(".page").forEach((s) => s.classList.toggle("on", s.id === "page-" + p));
  moveNav();
  if (S) { $$(".seg").forEach((s) => { const v = { segMode: S.mode, segSide: S.side, segLang: S.lang, segTheme: S.theme }[s.id]; paintSeg(s, v); }); }
  $("#main").scrollTop = 0;
}

/* ------------------------------------------------------------ share actions */
async function commitFields() {
  await api("set_config", { key: $("#keyInput").value, host: $("#hostInput").value });
}
let patchTimer;
function patchSoon(p) { clearTimeout(patchTimer); patchTimer = setTimeout(() => api("set_config", p), 350); }

/* ------------------------------------------------------------ file sending */
async function sendEntries(list) {
  if (!list.length) return;
  if (!target) return toast(t("no_target"), "warn");
  if (uploading) return;
  uploading = true;
  try {
    const res = await uploadSend(list, target);
    if (res && res.error) toast(res.error, "danger");
  } catch (e) { toast(String(e.message || e), "danger"); }
  uploading = false;
}


/* ---- drop zone shown while a file drag is held against a screen edge */
let ovTarget = null, ovBusy = false;
function showDropOv(m) {
  ovTarget = m; ovBusy = false;
  const el = $("#dropOv");
  el.className = ""; el.dataset.edge = m.edge; el.hidden = false;
  $("#dovTitle").textContent = t("drop_to", { name: m.key === "host" ? t("host_word") : m.name });
  $("#dovSub").textContent = t("drop_release");
}
function hideDropOv() { ovTarget = null; $("#dropOv").hidden = true; }
async function dropToOverlay(e) {
  if (ovBusy || !ovTarget) return;
  ovBusy = true;
  const el = $("#dropOv");
  el.classList.remove("over"); el.classList.add("sending");
  $("#dovTitle").textContent = t("drop_sending");
  try {
    const list = await collectEntries(e.dataTransfer);
    const res = await uploadSend(list, ovTarget.key, "drag");     // "drag": the drag continues on the other PC
    if (res && res.error) throw new Error(res.error);
    el.classList.remove("sending"); el.classList.add("done");
    $("#dovTitle").textContent = t("drop_sent");
    $("#dovSub").textContent = t("drag_hint");
  } catch (err) {
    el.classList.remove("sending");
    $("#dovTitle").textContent = String(err.message || err);
  }
}

/* ------------------------------------------------------------ remote viewer */
function showApprove(m) {
  $("#approveMsg").textContent = t("approve_msg", { name: m.name, ip: m.ip });
  const back = $("#approve");
  back.hidden = false;
  const done = (ok) => { back.hidden = true; api("approve", m.id, ok); clearTimeout(tm); };
  const tm = setTimeout(() => done(false), 34000);
  $("#btnAllow").onclick = () => done(true);
  $("#btnDeny").onclick = () => done(false);
}
const EXT = new Set([0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2d, 0x2e, 0x5b, 0x5c, 0x5d, 0x6f, 0x90]);
let vq = [], vraf = 0, vhide;
function vsend(line) {
  if (line[0] === "M" && vq.length && vq[vq.length - 1][0] === "M") vq[vq.length - 1] = line;
  else vq.push(line);
  if (!vraf) vraf = requestAnimationFrame(() => {
    vraf = 0;
    if (viewerId && vq.length) fetch("/api/rinput", { method: "POST", headers: { "X-Token": TOK }, body: JSON.stringify({ v: viewerId, text: vq.join("\n") }), keepalive: true });
    vq = [];
  });
}
function vpos(e) {
  const img = $("#vimg"), r = img.getBoundingClientRect();
  const nw = img.naturalWidth || 1, nh = img.naturalHeight || 1;
  const s = Math.min(r.width / nw, r.height / nh), dw = nw * s, dh = nh * s;
  const ox = r.left + (r.width - dw) / 2, oy = r.top + (r.height - dh) / 2;
  const cl = (v) => Math.max(0, Math.min(65535, Math.round(v * 65535)));
  return [cl((e.clientX - ox) / dw), cl((e.clientY - oy) / dh)];
}
const vmove = (e) => { if (!viewerId) return; vsend("M " + vpos(e).join(" ")); const b = $("#vbar"); b.classList.remove("hide"); clearTimeout(vhide); if (e.clientY > 90) vhide = setTimeout(() => b.classList.add("hide"), 1800); };
const vbtn = (down) => (e) => { if (!viewerId || e.target.closest(".vbar")) return; e.preventDefault(); vsend("M " + vpos(e).join(" ")); vsend(`B ${[0, 2, 1][e.button] ?? 0} ${down ? 1 : 0}`); };
const vwheel = (e) => {
  if (!viewerId) return; e.preventDefault();
  if (e.deltaY) vsend("W " + (-Math.sign(e.deltaY) * 120 * Math.max(1, Math.round(Math.abs(e.deltaY) / 100))));
  if (e.deltaX) vsend("H " + (Math.sign(e.deltaX) * 120));
};
const vkey = (down) => (e) => { if (!viewerId) return; e.preventDefault(); const vk = e.keyCode; if (vk >= 1 && vk <= 254) vsend(`K ${vk} 0 ${EXT.has(vk) ? 1 : 0} ${down ? 1 : 0}`); };
function openViewer(vid, host) {
  viewerId = vid;
  $("#vname").textContent = host;
  $("#vimg").src = `/stream?v=${vid}&t=${encodeURIComponent(TOK)}`;
  $("#viewer").hidden = false;
  $("#vbar").classList.remove("hide");
  vhide = setTimeout(() => $("#vbar").classList.add("hide"), 2500);
}
function closeViewer(notify = true) {
  if (!viewerId) return;
  const id = viewerId; viewerId = null;
  $("#viewer").hidden = true; $("#vimg").removeAttribute("src");
  if (document.fullscreenElement) document.exitFullscreen();
  if (notify) api("rclose", id);
}

/* ------------------------------------------------------------ wiring */
function wire() {
  $$("#nav button").forEach((b) => b.addEventListener("click", () => go(b.dataset.page)));
  addEventListener("resize", () => { moveNav(); $$(".seg").forEach((s) => paintSeg(s, { segMode: S.mode, segSide: S.side, segLang: S.lang, segTheme: S.theme }[s.id])); });
  matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => S && applyTheme());
  // share
  bindSeg("#segMode", (v) => api("set_config", { mode: v }));
  bindSeg("#segSide", (v) => api("set_config", { side: v }));
  $("#keyInput").addEventListener("input", (e) => patchSoon({ key: e.target.value }));
  $("#hostInput").addEventListener("input", (e) => patchSoon({ host: e.target.value }));
  $("#btnCopyKey").addEventListener("click", () => copyText($("#keyInput").value));
  $("#btnNewKey").addEventListener("click", () => api("new_key"));
  bindToggle("#tgClipText", (v) => api("set_config", { clip_text: v }));
  bindToggle("#tgClipFiles", (v) => api("set_config", { clip_files: v }));
  $("#btnShare").addEventListener("click", async () => { await commitFields(); api("share_toggle"); });
  $("#btnFind").addEventListener("click", (e) => {
    e.stopPropagation();
    const pop = $("#findPop"), ps = S.peers.filter((p) => p.share);
    if (!ps.length) return toast(t("no_hosts"), "warn");
    pop.innerHTML = ps.map((p) => `<button data-ip="${esc(p.ip)}"><span>${esc(p.name)}</span><small>${esc(p.ip)}</small></button>`).join("");
    pop.hidden = false;
    $$("button", pop).forEach((b) => b.addEventListener("click", () => { $("#hostInput").value = b.dataset.ip; api("set_config", { host: b.dataset.ip }); pop.hidden = true; }));
  });
  addEventListener("click", () => ($("#findPop").hidden = true));
  // files
  const drop = $("#drop"), fi = $("#fileInput");
  drop.addEventListener("click", (e) => { e.preventDefault(); if (target) fi.click(); else toast(t("no_target"), "warn"); });
  fi.addEventListener("change", () => { sendEntries([...fi.files].map((f) => ({ file: f, rel: f.name }))); fi.value = ""; });
  ["dragenter", "dragover"].forEach((ev) => addEventListener(ev, (e) => {
    e.preventDefault();
    if (ovTarget && !$("#dropOv").hidden) { if (!ovBusy) $("#dropOv").classList.add("over"); return; }
    if (page === "files") { drop.classList.add("over"); $("#dropTitle").textContent = t("drop_release"); }
  }));
  addEventListener("dragleave", (e) => { if (!e.relatedTarget) { drop.classList.remove("over"); $("#dropOv").classList.remove("over"); $("#dropTitle").textContent = t("drop_here"); } });
  addEventListener("drop", async (e) => {
    e.preventDefault(); drop.classList.remove("over"); $("#dropTitle").textContent = t("drop_here");
    if (ovTarget && !$("#dropOv").hidden) return dropToOverlay(e);
    if (page !== "files") go("files");
    sendEntries(await collectEntries(e.dataTransfer));
  });
  $("#btnOpenDl").addEventListener("click", () => api("open_downloads"));
  // remote
  $("#btnPwEye").addEventListener("click", () => {
    const i = $("#remotePw"), show = i.type === "password";
    i.type = show ? "text" : "password";
    $("#btnPwEye").innerHTML = svg(show ? "eyeoff" : "eye");
  });
  $("#btnPwCopy").addEventListener("click", () => copyText(S.remote.password));
  $("#btnPwNew").addEventListener("click", () => api("new_remote_pw"));
  bindToggle("#tgAllow", (v) => api("remote_allow", v));
  bindToggle("#tgApproval", (v) => api("set_config", { remote_approval: v }));
  const connect = async () => {
    const b = $("#btnConnect");
    if (b.classList.contains("busy")) return;
    b.classList.add("busy"); const old = b.innerHTML; b.innerHTML = svg("refresh") + `<span>${esc(t("connecting"))}</span>`;
    try {
      const r = await api("connect", $("#partnerInput").value, $("#partnerPw").value);
      if (r.vid) { openViewer(r.vid, r.host); $("#partnerPw").value = ""; }
      else toast(t(r.error || "r_failed", { msg: r.msg || r.error }), "danger");
    } catch (e) { toast(t("r_failed", { msg: e }), "danger"); }
    b.classList.remove("busy"); b.innerHTML = old;
  };
  $("#btnConnect").addEventListener("click", connect);
  $("#partnerPw").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
  // viewer
  const v = $("#viewer");
  v.addEventListener("mousemove", vmove); v.addEventListener("mousedown", vbtn(true)); v.addEventListener("mouseup", vbtn(false));
  v.addEventListener("wheel", vwheel, { passive: false }); v.addEventListener("contextmenu", (e) => e.preventDefault());
  addEventListener("keydown", vkey(true), true); addEventListener("keyup", vkey(false), true);
  addEventListener("blur", () => viewerId && vsend("R"));
  $("#btnVClose").addEventListener("click", () => closeViewer(true));
  $("#btnFs").addEventListener("click", () => document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen());
  // settings
  bindSeg("#segLang", (v) => api("set_config", { lang: v }));
  bindSeg("#segTheme", (v) => api("set_config", { theme: v }));
  bindToggle("#tgAutostart", (v) => api("set_autostart", v));
  bindToggle("#tgAutoUpd", (v) => api("set_config", { auto_update: v }));
  $("#btnInstall").addEventListener("click", () => api("install_here"));
  $("#btnFirewall").addEventListener("click", () => api("firewall"));
  $("#btnAdmin").addEventListener("click", () => api("restart_admin"));
  $("#btnCheck").addEventListener("click", () => api("check_updates", true));
  $("#updChip").addEventListener("click", () => go("settings"));
}

wire();
boot().catch((e) => { document.body.insertAdjacentHTML("beforeend", `<pre style="position:fixed;inset:20px;color:#f88;z-index:99">${esc(e.stack || e)}</pre>`); });
