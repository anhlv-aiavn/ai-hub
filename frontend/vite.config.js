import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: proxy /v1 → API (đổi target nếu chạy api ở cổng khác).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5273,
    proxy: {
      "/v1": { target: "http://localhost:18002", changeOrigin: true },
    },
  },
});
