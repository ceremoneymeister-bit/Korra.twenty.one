import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// Dedicated synthetic preview. Never imported by the product entry point.
const web = process.env.KORRA_PREVIEW_SOURCE || import.meta.dirname;
const ui = path.join(web, "src/vendor/nous-ui");
export default defineConfig({
  root: path.join(import.meta.dirname, "prototypes/interface"),
  base: "./",
  publicDir: path.join(web, "public"),
  plugins: [
    { name: "preview-source-scan", enforce: "pre", transform(code, id) {
      if (id === path.join(web, "src/index.css")) return code.replace("@import 'tailwindcss';", `@import 'tailwindcss';\n@source "${path.join(web, "src")}";`);
    } },
    react(), tailwindcss(),
  ],
  resolve: {
    alias: [
      { find: /^@nous-research\/ui\/styles\/(fonts|globals)\.css$/, replacement: `${ui}/ui/$1.css` },
      { find: "@nous-research/ui", replacement: ui },
      { find: "@", replacement: path.join(web, "src") },
      { find: "@hermes/shared", replacement: path.join(web, "../apps/shared/src") },
    ],
    dedupe: ["react", "react-dom", "react-router", "nanostores", "@nanostores/react"],
  },
  build: { outDir: process.env.KORRA_PREVIEW_OUT || "../../../../preview-dist", emptyOutDir: true, sourcemap: false },
  server: { host: "127.0.0.1", port: 5184, strictPort: true },
});
