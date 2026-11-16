"use strict";
/* Edge drop panel: a real OS drop target shown while a file drag is held against a screen edge */
let STR = {}, LANG = "en", tgt = null, busy = false;
const t = (k, kw) => {
  let s = (STR[LANG] || {})[k] ?? (STR.en || {})[k] ?? k;
  return kw ? s.replace(/\{(\w+)\}/g, (_, a) => kw[a] ?? "") : s;
};
const $ = (s) => document.querySelector(s);

function render() {
  if (!tgt) return;
  const name = tgt.key === "host" ? t("host_word") : tgt.name;
  document.body.dataset.edge = tgt.edge;
  $("#ovTitle").textContent = busy ? t("drop_sending") : t("drop_to", { name });
  $("#ovSub").textContent = t("drop_release");
  const ov = $(".ov");
  ov.style.animation = "none"; void ov.offsetWidth; ov.style.animation = "";   // replay the slide-in
}

async function boot() {
  const d = await (await fetch("/api/boot", { headers: { "X-Token": TOK } })).json();
  STR = d.strings; LANG = d.state.lang;
  const es = new EventSource("/api/events?t=" + encodeURIComponent(TOK));
  es.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.k === "overlay") { tgt = m; busy = false; document.body.className = ""; render(); }
    else if (m.k === "state") { LANG = m.state.lang; }
  };
}

["dragenter", "dragover"].forEach((ev) => addEventListener(ev, (e) => { e.preventDefault(); if (!busy) document.body.classList.add("over"); }));
addEventListener("dragleave", (e) => { if (!e.relatedTarget) document.body.classList.remove("over"); });
addEventListener("drop", async (e) => {
  e.preventDefault();
  if (!tgt || busy) return;
  busy = true;
  document.body.classList.remove("over"); document.body.classList.add("sending");
  $("#ovTitle").textContent = t("drop_sending");
  try {
    const list = await collectEntries(e.dataTransfer);
    const res = await uploadSend(list, tgt.key);
    if (res && res.error) throw new Error(res.error);
    document.body.classList.remove("sending"); document.body.classList.add("done");
    $("#ovTitle").textContent = t("drop_sent");
  } catch (err) {
    document.body.classList.remove("sending");
    $("#ovTitle").textContent = String(err.message || err);
  }
  busy = false;
});
boot();
