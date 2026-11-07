// Turns Vite's single-file build into an artifact page body.
// The artifact host wraps the file in its own <!doctype><html><head><body>,
// so this keeps only what goes inside: <title> first (only the first 8 KB is
// scanned for it), then metas, font links, styles, the root element and scripts.
import { readFileSync, writeFileSync } from "node:fs";

const src = readFileSync(new URL("../dist/index.html", import.meta.url), "utf8");

const pick = (re) => src.match(re)?.[0] ?? "";
const pickAll = (re) => [...src.matchAll(re)].map((m) => m[0]);

const head = pick(/<head>([\s\S]*?)<\/head>/i);
const body = pick(/<body>([\s\S]*?)<\/body>/i);
if (!head || !body) throw new Error("dist/index.html has no <head> or <body>; did the build run?");

const title = pick(/<title>[\s\S]*?<\/title>/i);
if (!title) throw new Error("No <title> in the build output.");
const sidesMeta = pick(/<meta name="claude:viewport-fit-sides"[^>]*>/i);
const links = pickAll(/<link\s[^>]*>/gi);
const styles = pickAll(/<style[^>]*>[\s\S]*?<\/style>/gi);
const headScripts = pickAll(/<script[^>]*>[\s\S]*?<\/script>/gi).filter((s) => head.includes(s));
const bodyInner = body.replace(/^<body>/i, "").replace(/<\/body>$/i, "").trim();

// Head scripts are module scripts that expect #root; run them after it exists.
const out = [title, sidesMeta, ...links, ...styles, bodyInner, ...headScripts].filter(Boolean).join("\n");

if (/<html|<\/html>|<body|<!doctype/i.test(out)) throw new Error("Wrapper tags survived; the artifact skeleton adds its own.");
writeFileSync(new URL("../dist/artifact.html", import.meta.url), out + "\n");
console.log(`dist/artifact.html written (${(out.length / 1024).toFixed(1)} KB)`);
