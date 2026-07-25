import { Card, Form, Input, Button, Typography, message } from "antd";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const onFinish = async (v: { apiKey: string; base?: string }) => {
    try {
      await login(v.apiKey, v.base);
      nav("/kbs");
    } catch {
      message.error("登录失败：API-key 无效或后端不可达");
    }
  };
  return (
    <div style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "100vh", background: "#f0f2f5" }}>
      <Card title="kb-backend 控制台登录" style={{ width: 420 }}>
        <Form onFinish={onFinish} layout="vertical" initialValues={{ apiKey: "kb_dev_api_key" }}>
          <Form.Item name="apiKey" label="API Key" rules={[{ required: true }]}>
            <Input.Password placeholder="kb_dev_api_key" />
          </Form.Item>
          <Form.Item name="base" label="后端地址（留空=dev proxy :8001）">
            <Input placeholder="http://localhost:8001" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>登录</Button>
          <Typography.Text type="secondary" style={{ display: "block", marginTop: 12, fontSize: 12 }}>
            本地开发默认 kb_dev_api_key；后端跑在 :8001，Vite proxy 已配同源。
          </Typography.Text>
        </Form>
      </Card>
    </div>
  );
}
