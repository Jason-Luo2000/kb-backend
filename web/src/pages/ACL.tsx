import { Form, Input, Select, Button, Card, Space, Typography, message } from "antd";
import { grant, revoke } from "../api";

export default function ACL() {
  const [gf] = Form.useForm();
  const [rf] = Form.useForm();
  const doGrant = async (v: { kbId: string; userId: string; role?: string; expiresAt?: string }) => {
    try {
      await grant(v.kbId, v.userId, v.role || "viewer", v.expiresAt || undefined);
      message.success("已授权");
      gf.resetFields();
    } catch {
      /* 错误已由拦截器提示 */
    }
  };
  const doRevoke = async (v: { kbId: string; userId: string }) => {
    try {
      await revoke(v.kbId, v.userId);
      message.success("已撤销");
      rf.resetFields();
    } catch {
      /* 同上 */
    }
  };
  return (
    <>
      <Typography.Title level={3}>授权管理（admin）</Typography.Title>
      <Space direction="vertical" style={{ width: "100%" }}>
        <Card title="授权（grant）" size="small">
          <Form form={gf} layout="inline" onFinish={doGrant}>
            <Form.Item name="kbId" rules={[{ required: true }]}>
              <Input placeholder="kbId" />
            </Form.Item>
            <Form.Item name="userId" rules={[{ required: true }]}>
              <Input placeholder="userId (UUID)" />
            </Form.Item>
            <Form.Item name="role" initialValue="viewer">
              <Select style={{ width: 110 }} options={[{ value: "viewer" }, { value: "editor" }, { value: "admin" }]} />
            </Form.Item>
            <Form.Item name="expiresAt">
              <Input placeholder="expiresAt ISO（可选）" />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit">
                授权
              </Button>
            </Form.Item>
          </Form>
        </Card>
        <Card title="撤销（revoke）" size="small">
          <Form form={rf} layout="inline" onFinish={doRevoke}>
            <Form.Item name="kbId" rules={[{ required: true }]}>
              <Input placeholder="kbId" />
            </Form.Item>
            <Form.Item name="userId" rules={[{ required: true }]}>
              <Input placeholder="userId (UUID)" />
            </Form.Item>
            <Form.Item>
              <Button danger htmlType="submit">
                撤销
              </Button>
            </Form.Item>
          </Form>
        </Card>
      </Space>
    </>
  );
}
