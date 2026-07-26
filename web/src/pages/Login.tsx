import { Card, Form, Input, Button, Typography, message } from "antd";
import { LockOutlined } from "@ant-design/icons";
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
    <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", minHeight: "100vh", padding: 24 }}>
      <div className="kb-brand" style={{ padding: 0, marginBottom: 8 }}>
        <div className="kb-brand-mark" style={{ width: 42, height: 42, fontSize: 20 }}>KB</div>
        <div>
          <div className="kb-brand-text" style={{ fontSize: 22 }}>kb 控制台</div>
          <div className="kb-brand-sub">Dual-Path RAG Console</div>
        </div>
      </div>
      <Card className="kb-glass" style={{ width: 420, backdropFilter: "blur(14px)" }}>
        <Typography.Title level={4} className="kb-title" style={{ textAlign: "center", marginBottom: 20 }}>
          登录
        </Typography.Title>
        <Form onFinish={onFinish} layout="vertical" initialValues={{ apiKey: "" }}>
          <Form.Item name="apiKey" label="API Key" rules={[{ required: true }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="你的 KB_API_KEY" />
          </Form.Item>
          <Form.Item name="base" label="后端地址（留空 = dev proxy :8001）">
            <Input placeholder="http://localhost:8001" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large">进入控制台</Button>
          <Typography.Text type="secondary" style={{ display: "block", marginTop: 16, fontSize: 12, textAlign: "center" }}>
            部署用 <code>./deploy.sh</code> 打印的 key（bare-metal 默认 <code>kb_dev_api_key</code>）；后端 :8001，Vite proxy 同源。
          </Typography.Text>
        </Form>
      </Card>
    </div>
  );
}
