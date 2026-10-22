#!/usr/bin/env node
// R3.8 — the built client bundle must ship a working service worker (web push depends on it).
// Usage: node ci/pwa_bundle_check.js [frontend/dist]
const fs = require("fs");
const path = require("path");

const dist = process.argv[2] || "frontend/dist";
const fail = [];
const read = (f) => { try { return fs.readFileSync(path.join(dist, f), "utf8"); } catch { fail.push(`missing ${f}`); return ""; } };

const html = read("index.html");
const reg = read("registerSW.js");
const sw = read("sw.js");
read("push-handler.js");
const manifest = read("manifest.webmanifest");
if (!fs.readdirSync(dist).some((f) => /^workbox-[\w-]+\.js$/.test(f))) fail.push("missing workbox-*.js");
if (html && !html.includes('src="/registerSW.js"')) fail.push("index.html doesn't load /registerSW.js");
if (html && !html.includes("manifest.webmanifest")) fail.push("index.html doesn't link the manifest");
if (reg && !/serviceWorker\.register\(['"]\/sw\.js['"]/.test(reg)) fail.push("registerSW.js doesn't register /sw.js");
if (sw && !sw.includes("push-handler.js")) fail.push("sw.js doesn't importScripts /push-handler.js");
if (manifest) { try { JSON.parse(manifest); } catch { fail.push("manifest.webmanifest isn't valid JSON"); } }

if (fail.length) { console.error("pwa bundle check FAILED:\n  " + fail.join("\n  ")); process.exit(1); }
console.log(`pwa bundle check OK (${dist}): registerSW.js → sw.js (+ push-handler.js, workbox, manifest)`);
