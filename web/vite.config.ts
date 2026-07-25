import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// dev: :5173，/v1 /healthz /readyz /metrics 代理到后端 :8001（同源，免 CORS）
// prod: VITE_API_BASE 指向后端，或后端挂 StaticFiles 单端口
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://localhost:8001",
      "/healthz": "http://localhost:8001",
      "/readyz": "http://localhost:8001",
      "/metrics": "http://localhost:8001",
    },
  },
});
