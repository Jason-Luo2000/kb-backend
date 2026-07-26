import { Layout as AntLayout, Menu, Avatar, Dropdown, Typography, Spin, Tag } from "antd";
import { useNavigate, Navigate, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  BookOutlined,
  MessageOutlined,
  SafetyOutlined,
  ControlOutlined,
  DashboardOutlined,
  LogoutOutlined,
  UserOutlined,
  RobotOutlined,
  FolderOpenOutlined,
} from "@ant-design/icons";
import { getMe } from "../api";
import { useAuth } from "../context/AuthContext";

const { Sider, Header, Content } = AntLayout;

export default function Layout() {
  const { apiKey, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();

  // 未登录 → /login。401 由 client 拦截器也兜底跳转
  if (!apiKey) return <Navigate to="/login" replace />;

  const { data: me, isLoading } = useQuery({
    queryKey: ["me"],
    queryFn: getMe,
    retry: false,
    staleTime: 60_000,
  });

  if (isLoading) return <Spin fullscreen />;
  if (!me) return <Navigate to="/login" replace />;

  const items = [
    { key: "/kbs", icon: <BookOutlined />, label: "知识库" },
    { key: "/files", icon: <FolderOpenOutlined />, label: "文件库" },
    { key: "/chat", icon: <MessageOutlined />, label: "问答" },
  ];
  if (me.is_admin) items.push({ key: "/acl", icon: <SafetyOutlined />, label: "授权" });
  if (me.is_owner) {
    items.push({ key: "/models", icon: <RobotOutlined />, label: "模型" });
    items.push({ key: "/ops", icon: <ControlOutlined />, label: "运维" });
    items.push({ key: "/monitor", icon: <DashboardOutlined />, label: "监控" });
  }
  const selected = "/" + (loc.pathname.split("/")[1] || "kbs");

  return (
    <AntLayout style={{ minHeight: "100vh" }}>
      <Sider className="kb-sider" collapsible breakpoint="lg" theme="dark" width={224}>
        <div className="kb-brand">
          <div className="kb-brand-mark">KB</div>
          <div>
            <div className="kb-brand-text">kb 控制台</div>
            <div className="kb-brand-sub">Dual-Path RAG</div>
          </div>
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[selected]} items={items} onClick={({ key }) => nav(key)} />
      </Sider>
      <AntLayout>
        <Header className="kb-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0 24px" }}>
          <Typography.Text style={{ color: "#8aa0c0", letterSpacing: 1, fontSize: 13 }}>
            {/* 面包屑占位：当前模块 */}
            {items.find((i) => i.key === selected)?.label || ""}
          </Typography.Text>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Tag color={me.is_owner ? "cyan" : me.is_admin ? "blue" : "default"} style={{ margin: 0 }}>
              {me.is_owner ? "owner" : me.is_admin ? "admin" : "user"}
            </Tag>
            <Typography.Text style={{ color: "#cbd5e1" }}>{me.user_id}</Typography.Text>
            <Dropdown
              menu={{
                items: [
                  {
                    key: "logout",
                    icon: <LogoutOutlined />,
                    label: "退出登录",
                    onClick: () => {
                      logout();
                      nav("/login");
                    },
                  },
                ],
              }}
            >
              <Avatar style={{ cursor: "pointer", background: "linear-gradient(135deg,#22d3ee,#6366f1)" }} icon={<UserOutlined />} />
            </Dropdown>
          </div>
        </Header>
        <Content className="kb-fade" style={{ margin: 24 }}>
          <Outlet />
        </Content>
      </AntLayout>
    </AntLayout>
  );
}
