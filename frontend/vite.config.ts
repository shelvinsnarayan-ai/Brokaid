import { defineConfig, loadEnv } from "vite";
import type { Plugin } from "vite";
import { execSync } from "node:child_process";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

/**
 * brokAId — frontend Vite config.
 *
 * Guards:
 *  - Ground rule: `AUTH_DEV_MODE=true` MUST NOT compile into a
 *    production build. Enforced by `productionDevAuthGuard` below.
 *  - The `/dev/login` chunk is tagged with `/*! brokaid:dev-only *\/`
 *    so the CI deny-list scanner can skip it.
 */

function productionDevAuthGuard(): Plugin {
  return {
    name: "brokaid-dev-auth-prod-guard",
    apply: "build",
    enforce: "pre",
    configResolved(config) {
      const nodeEnv = process.env.NODE_ENV ?? config.mode;
      const authDev = (process.env.AUTH_DEV_MODE ?? "false").toLowerCase();
      if (nodeEnv === "production" && authDev === "true") {
        throw new Error(
          "[brokAId] Refusing to build: AUTH_DEV_MODE=true with NODE_ENV=production. " +
            "Dev auth must never ship. Set AUTH_DEV_MODE=false to build for production."
        );
      }
    },
  };
}

function readBuildIdentity(): { sha: string; signed: "G" | "N" | "?" } {
  // Phase 17 — bake the current git SHA (7-char) and signature status
  // into the client bundle so the operator shell can render a
  // "build 51150d3 · G" credibility indicator. Falls back to "unknown"
  // when git is unavailable so the build never fails on a source-only
  // checkout. NEVER read from a runtime API — build-time only.
  const sha = ((): string => {
    try {
      return execSync("git rev-parse --short=7 HEAD", {
        cwd: process.cwd(), stdio: ["ignore", "pipe", "ignore"],
      }).toString().trim() || "unknown";
    } catch { return "unknown"; }
  })();
  const signed = ((): "G" | "N" | "?" => {
    try {
      // %G? = 'G' good, 'B' bad, 'U' good-untrusted, 'X' expired,
      //       'Y' expired-key, 'R' revoked, 'E' cannot check, 'N' no signature.
      const out = execSync("git log -1 --pretty=%G?", {
        cwd: process.cwd(), stdio: ["ignore", "pipe", "ignore"],
      }).toString().trim();
      if (out === "G") return "G";
      if (out === "N" || out === "") return "N";
      return "?";
    } catch { return "?"; }
  })();
  return { sha, signed };
}

function devOnlyChunkMarker(): Plugin {
  // Tag the dev-only chunk (the /dev/login route module) with a
  // deterministic marker comment. `ci/deny-list.js` skips any file
  // containing this marker.
  return {
    name: "brokaid-dev-only-chunk-marker",
    generateBundle(_options, bundle) {
      for (const [fileName, chunk] of Object.entries(bundle)) {
        if (chunk.type !== "chunk") continue;
        const isDevChunk = Object.keys(chunk.modules).some((id) =>
          /DevLogin|DevAuthBanner/.test(id)
        );
        if (isDevChunk) {
          chunk.code = `/*! brokaid:dev-only */\n` + chunk.code;
          // ensure filename hints too
          void fileName;
        }
      }
    },
  };
}

const SECURITY_HEADERS = {
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
  "X-Frame-Options": "DENY",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
};

function csp(dev: boolean): string {
  const script = dev ? "'self' 'unsafe-inline'" : "'self'";
  return [`default-src 'self'`, `script-src ${script}`, `style-src 'self' 'unsafe-inline' https://fonts.googleapis.com`,
    `font-src 'self' https://fonts.gstatic.com data:`, `img-src 'self' data: blob:`, `connect-src 'self' wss: https:`,
    `worker-src 'self'`, `manifest-src 'self'`, `frame-ancestors 'none'`, `base-uri 'self'`, `form-action 'self'`, `object-src 'none'`].join("; ");
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const authDev = (env.AUTH_DEV_MODE ?? "false").toLowerCase() === "true";
  // Fail-closed like the backend: unset/unknown ENVIRONMENT is production → the demo panel is compiled out.
  const demoSignIn = ["development", "preview", "test"].includes((env.ENVIRONMENT ?? "").trim().toLowerCase());
  return {
    plugins: [
      productionDevAuthGuard(),
      react(),
      authDev ? devOnlyChunkMarker() : null,
      VitePWA({
        registerType: "autoUpdate",
        // R0.13: push + notificationclick handlers; caching/auto-update stay with the generated SW.
        workbox: { importScripts: ["/push-handler.js"] },
        includeAssets: [
          "favicon-app.svg",
          "favicon-platform.svg",
          "brand/*.svg",
          "pwa/*.png",
        ],
        // Serve the generated `manifest.webmanifest` (with the correct
        // `application/manifest+json` Content-Type) and register the
        // service-worker in the Vite dev server so the client `/c/*`
        // surface can be installed as a PWA in preview environments,
        // not just after a production build.
        devOptions: {
          enabled: true,
          type: "module",
          navigateFallback: "index.html",
          suppressWarnings: true,
        },
        manifest: {
          name: "brokAId",
          short_name: "brokAId",
          description: "Insurance broker OS — client",
          theme_color: "#0E2B34",
          background_color: "#F3EBDA",
          scope: "/c/",
          start_url: "/c/",
          display: "standalone",
          icons: [
            {
              src: "/favicon-app.svg",
              sizes: "any",
              type: "image/svg+xml",
              purpose: "any",
            },
            {
              src: "/pwa/pwa-192.png",
              sizes: "192x192",
              type: "image/png",
              purpose: "any",
            },
            {
              src: "/pwa/pwa-512.png",
              sizes: "512x512",
              type: "image/png",
              purpose: "any",
            },
            {
              src: "/pwa/pwa-maskable-512.png",
              sizes: "512x512",
              type: "image/png",
              purpose: "maskable",
            },
          ],
        },
      }),
    ],
    define: {
      "import.meta.env.BROKAID_AUTH_DEV_MODE": JSON.stringify(authDev),
      "import.meta.env.BROKAID_DEMO_SIGNIN": JSON.stringify(demoSignIn),
      "import.meta.env.BROKAID_BUILD_SHA": JSON.stringify(readBuildIdentity().sha),
      "import.meta.env.BROKAID_BUILD_SIGNED": JSON.stringify(readBuildIdentity().signed),
    },
    // R1.9: security headers. Dev needs 'unsafe-inline' scripts for the React refresh preamble; preview/prod does not.
    preview: { headers: { ...SECURITY_HEADERS, "Content-Security-Policy": csp(false) } },
    server: {
      headers: { ...SECURITY_HEADERS, "Content-Security-Policy": csp(true) },
      host: "0.0.0.0",
      port: 3000,
      strictPort: true,
      allowedHosts: true,
      hmr: {
        clientPort: 443,
      },
      proxy: {
        "/api": {
          target: "http://localhost:8001",
          changeOrigin: false,
        },
      },
    },
    build: {
      target: "es2022",
      sourcemap: false,
      rollupOptions: {
        output: authDev ? {
          manualChunks(id) {
            if (id.includes("DevLogin") || id.includes("DevAuthBanner")) {
              return "dev-auth";
            }
          },
        } : {},  // production: dev pages are compiled out, so no dev-auth chunk exists to split
      },
    },
  };
});
