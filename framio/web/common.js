"use strict";
/* Helpers shared by the main window and the edge drop panel */
const TOK = document.querySelector("meta[name=tok]").content;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(name, ...args) {
  const r = await fetch("/api/call/" + name, { method: "POST", headers: { "X-Token": TOK, "Content-Type": "application/json" }, body: JSON.stringify({ args }) });
  return r.json();
}

/* Expand a drop (files and folders) into [{file, rel}] */
async function collectEntries(dt) {
  const out = [];
  const walk = async (entry, prefix) => {
    if (entry.isFile) {
      const f = await new Promise((res, rej) => entry.file(res, rej));
      out.push({ file: f, rel: prefix + f.name });
    } else if (entry.isDirectory) {
      const rd = entry.createReader();
      let batch;
      do {
        batch = await new Promise((res, rej) => rd.readEntries(res, rej));
        for (const e of batch) await walk(e, prefix + entry.name + "/");
      } while (batch.length);
    }
  };
  const items = dt.items ? [...dt.items] : [];
  const ents = items.map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (ents.length) { for (const e of ents) await walk(e, ""); }
  else for (const f of dt.files) out.push({ file: f, rel: f.name });
  return out;
}

/* Upload the files to the local app, then ask it to send them to `target` */
async function uploadSend(list, target, mode = "file") {
  const batch = [...crypto.getRandomValues(new Uint8Array(6))].map((b) => b.toString(16).padStart(2, "0")).join("");
  for (const { file, rel } of list) {
    const r = await fetch(`/api/upload?b=${batch}&rel=${encodeURIComponent(rel)}`, { method: "POST", headers: { "X-Token": TOK }, body: file });
    if (!r.ok) throw new Error("upload failed");
  }
  return api("send", batch, target, [], mode);
}
