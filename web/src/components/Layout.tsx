import { Layout as AntLayout, Menu, Avatar, Dropdown, Typography, Spin } from "antd";
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
    { key: "/chat", icon: <MessageOutlined />, label: "问答" },
  ];
  if (me.is_admin) items.push({ key: "/acl", icon: <SafetyOutlined />, label: "授权" });
  if (me.is_owner) items.push({ key: "/models", icon: <RobotOutlined />, label: "模型" });
  if (me.is_owner) {
    items.push({ key: "/ops", icon: <ControlOutlined />, label: "运维" });
    items.push({ key: "/monitor", icon: <DashboardOutlined />, label: "监控" });
  }
  const selected = "/" + (loc.pathname.split("/")[1] || "kbs");

  return (
    <AntLayout style={{ minHeight: "100vh" }}>
      <Sider collapsible breakpoint="lg">
        <div style={{ color: "#fff", padding: 16, textAlign: "center", fontWeight: 600 }}>kb 控制台</div>
        <Menu theme="dark" mode="inline" selectedKeys={[selected]} items={items} onClick={({ key }) => nav(key)} />
      </Sider>
      <AntLayout>
        <Header style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", background: "#fff", padding: "0 24px" }}>
          <Typography.Text type="secondary" style={{ marginRight: 12 }}>
            {me.user_id} {me.is_owner ? "· owner" : me.is_admin ? "· admin" : ""}
          </Typography.Text>
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
            <Avatar style={{ cursor: "pointer" }} icon={<UserOutlined />} />
          </Dropdown>
        </Header>
        <Content style={{ margin: 24, padding: 24, background: "#fff", borderRadius: 8 }}>
          <Outlet />
        </Content>
      </AntLayout>
    </AntLayout>
  );
}
