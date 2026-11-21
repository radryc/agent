import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3000,
    // Plaintext by default. When the backend runs with STRATA_TLS_MODE=required,
    // change these targets to https://localhost:8090 and set secure: false for a
    // local self-signed CA (never outside local development).
    proxy: {
      "/api": "http://localhost:8090",
      "/ws": {
        target: "ws://localhost:8090",
        ws: true,
      },
    },
  },
});
