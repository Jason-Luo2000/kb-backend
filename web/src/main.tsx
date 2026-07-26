import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import { AuthProvider } from "./context/AuthContext";
import "./index.css";

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

// 深空科技主题：深蓝底 + 青蓝辉光 + 玻璃拟态
const techTheme = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: "#22d3ee",
    colorInfo: "#22d3ee",
    colorLink: "#38bdf8",
    colorSuccess: "#34d399",
    colorWarning: "#fbbf24",
    colorError: "#f87171",
    colorBgBase: "#0a0e1a",
    colorTextBase: "#e2e8f0",
    colorBgContainer: "#141b2d",
    colorBgElevated: "#1a2238",
    colorBgLayout: "#0a0e1a",
    colorBorder: "rgba(148,163,184,0.18)",
    colorBorderSecondary: "rgba(148,163,184,0.10)",
    borderRadius: 10,
    fontFamily:
      "'Inter','SF Pro Display',-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif",
    fontSize: 14,
    boxShadowSecondary: "0 8px 30px rgba(0,0,0,0.5)",
  },
  components: {
    Layout: {
      siderBg: "transparent",
      headerBg: "transparent",
      bodyBg: "transparent",
      triggerBg: "#0f1729",
    },
    Menu: {
      darkItemBg: "transparent",
      darkSubMenuItemBg: "transparent",
      darkItemSelectedBg: "rgba(34,211,238,0.16)",
      darkItemHoverBg: "rgba(34,211,238,0.08)",
      darkItemSelectedColor: "#22d3ee",
      darkItemColor: "#8aa0c0",
      itemBorderRadius: 8,
      itemMarginInline: 8,
    },
    Card: {
      colorBgContainer: "rgba(20,27,45,0.72)",
      colorBorderSecondary: "rgba(148,163,184,0.14)",
    },
    Table: {
      headerBg: "rgba(34,211,238,0.07)",
      headerColor: "#67e8f9",
      rowHoverBg: "rgba(34,211,238,0.05)",
      borderColor: "rgba(148,163,184,0.12)",
      headerSplitColor: "transparent",
    },
    Modal: { contentBg: "#141b2d", headerBg: "#141b2d" },
    Button: { primaryShadow: "none", defaultBorderColor: "rgba(148,163,184,0.25)" },
    Input: { activeShadow: "0 0 0 2px rgba(34,211,238,0.15)" },
    Tag: { defaultBg: "rgba(34,211,238,0.10)" },
  },
};

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={techTheme}>
      <QueryClientProvider client={qc}>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ConfigProvider>
  </React.StrictMode>
);
