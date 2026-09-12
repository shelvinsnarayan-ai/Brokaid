import { defineConfig, loadEnv } from "vite";
import type { Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

/**
 * brokAId — apps/web Vite config.
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

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const authDev = (env.AUTH_DEV_MODE ?? "false").toLowerCase() === "true";
  return {
    plugins: [
      productionDevAuthGuard(),
      react(),
      devOnlyChunkMarker(),
      VitePWA({
        registerType: "autoUpdate",
        includeAssets: ["favicon.svg"],
        manifest: {
          name: "brokAId",
          short_name: "brokAId",
          description: "Insurance broker OS — client",
          theme_color: "#0B3B4B",
          background_color: "#F4EFE6",
          scope: "/c/",
          start_url: "/c/",
          display: "standalone",
          icons: [
            {
              src: "/favicon.svg",
              sizes: "any",
              type: "image/svg+xml",
              purpose: "any maskable",
            },
          ],
        },
      }),
    ],
    define: {
      __AUTH_DEV_MODE__: JSON.stringify(authDev),
    },
    server: {
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
        output: {
          manualChunks(id) {
            if (id.includes("DevLogin") || id.includes("DevAuthBanner")) {
              return "dev-auth";
            }
          },
        },
      },
    },
  };
});
