import axios, { AxiosError } from "axios";
import { message } from "antd";

// dev: baseURL 空（Vite proxy 同源）；prod: VITE_API_BASE 指向后端
export const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE || "" });

api.interceptors.request.use((cfg) => {
  const key = localStorage.getItem("kb_api_key");
  if (key) cfg.headers.Authorization = `Bearer ${key}`;
  return cfg;
});

api.interceptors.response.use(
  (r) => r,
  (err: AxiosError) => {
    const status = err.response?.status;
    const detail = (err.response?.data as { detail?: unknown })?.detail;
    const msg = typeof detail === "string" ? detail : err.message || "请求失败";
    if (status === 401) {
      localStorage.removeItem("kb_api_key");
      if (window.location.pathname !== "/login") window.location.href = "/login";
    } else {
      message.error(msg);
    }
    return Promise.reject(err);
  }
);
