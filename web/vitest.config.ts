import { defineConfig } from "vitest/config";
import babel from "@rolldown/plugin-babel";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";

/** Same component/hook-scoped compiler preset as vite.config.ts. */
function compilerPreset() {
  const preset = reactCompilerPreset();
  preset.rolldown.filter.code = /\/>|<\/|from\s*['"][^'"]*react/;
  return preset;
}
import path from "path";

const NOUS_UI_SRC = path.resolve(import.meta.dirname, "./src/vendor/nous-ui");

export default defineConfig({
  plugins: [react(), babel({ presets: [compilerPreset()] })],
  resolve: {
    alias: [
      {
        find: /^@nous-research\/ui\/styles\/(fonts|globals)\.css$/,
        replacement: `${NOUS_UI_SRC}/ui/$1.css`,
      },
      { find: "@nous-research/ui", replacement: NOUS_UI_SRC },
      { find: "@", replacement: path.resolve(import.meta.dirname, "./src") },
    ],
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
