// @ts-check
import { defineConfig } from "astro/config";

import react from "@astrojs/react";

// https://astro.build/config
export default defineConfig({
  // Astro 5 defaults to static output — all pages are pre-rendered at build
  // time. No adapter needed. The content layer fetches from Django at build
  // time and bakes everything into static HTML.
  output: "static",

  integrations: [react()],
});