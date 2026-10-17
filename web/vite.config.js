import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 127.0.0.1 (not "localhost") avoids Windows resolving to ::1 while uvicorn listens on IPv4.
const BACKEND = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    allowedHosts: true, // lets an ngrok hostname through
    proxy: {
      "/api": BACKEND,
      "/ws": { target: BACKEND, ws: true },
    },
  },
});
