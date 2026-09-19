/**
 * Generate PNG PWA icons from `public/favicon-app.svg`.
 *
 * Older Android launchers and iOS home-screen installs can require
 * raster; VitePWA references these files from its manifest icons
 * array. Idempotent — writes only if input has changed.
 *
 * Outputs:
 *   public/pwa/pwa-192.png            (any purpose)
 *   public/pwa/pwa-512.png            (any purpose)
 *   public/pwa/pwa-maskable-512.png   (maskable — 20% safe-zone padding)
 *
 * Run:  node scripts/generate-pwa-icons.mjs
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_FRONTEND = resolve(__dirname, "..");
const SRC = resolve(REPO_FRONTEND, "public/favicon-app.svg");
const OUT_DIR = resolve(REPO_FRONTEND, "public/pwa");

mkdirSync(OUT_DIR, { recursive: true });

const svgBuf = readFileSync(SRC);

/**
 * Any-purpose icons — the pulse tile fills the canvas at 100%.
 * Sand background so the icon doesn't disappear on light launchers
 * (matches the tile fill; ends up as edge-to-edge Harbour tile).
 */
async function renderAny(size) {
  const buf = await sharp(svgBuf, { density: 512 })
    .resize(size, size, { fit: "contain", background: { r: 243, g: 235, b: 218, alpha: 1 } })
    .png()
    .toBuffer();
  const out = resolve(OUT_DIR, `pwa-${size}.png`);
  writeFileSync(out, buf);
  console.log(`  wrote ${out} (${buf.length} bytes)`);
}

/**
 * Maskable variant — 20% safe-zone padding per Google's spec so the
 * pulse mark survives Android launcher circle / squircle / teardrop
 * masks without amputation.
 */
async function renderMaskable(size) {
  const inner = Math.round(size * 0.6); // 20% padding on all sides
  const padded = await sharp(svgBuf, { density: 512 })
    .resize(inner, inner, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .toBuffer();
  const buf = await sharp({
    create: {
      width: size, height: size, channels: 4,
      background: { r: 14, g: 43, b: 52, alpha: 1 }, // Harbour #0E2B34
    },
  })
    .composite([{ input: padded, gravity: "center" }])
    .png()
    .toBuffer();
  const out = resolve(OUT_DIR, `pwa-maskable-${size}.png`);
  writeFileSync(out, buf);
  console.log(`  wrote ${out} (${buf.length} bytes)`);
}

console.log(`Generating PWA icons from ${SRC}`);
await renderAny(192);
await renderAny(512);
await renderMaskable(512);
console.log("Done.");

// Silence unused warning when someone bundles by mistake.
export {};
