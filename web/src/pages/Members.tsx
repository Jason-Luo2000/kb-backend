import { useState } from "react";
import { Table, Button, Space, Tag, Typography, Modal, Form, Input, Select, Drawer, Popconfirm, message, Alert } from "antd";
import { UserAddOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listUsers, listDepartments, createUser, updateUser, deleteUser, userKbs, bulkGrant, revoke, listKbs,
} from "../api";
import type { Member } from "../types";

const ROLE_COLOR: Record<string, string> = { owner: "red", admin: "gold", editor: "blue", viewer: "default" };

export default function Members() {
  const qc = useQueryClient();
  const [dept, setDept] = useState<string | undefined>();
  const { data: users, isLoading } = useQuery({ queryKey: ["users", dept], queryFn: () => listUsers(dept) });
  const { data: depts } = useQuery({ queryKey: ["departments"], queryFn: listDepartments });
  const { data: kbs } = useQuery({ queryKey: ["kbs"], queryFn: listKbs });

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Member | null>(null);
  const [form] = Form.useForm();
  const [newKey, setNewKey] = useState<string | null>(null);
  const [kbUser, setKbUser] = useState<Member | null>(null);
  const [grantKbs, setGrantKbs] = useState<string[]>([]);
  const [grantRole, setGrantRole] = useState("viewer");
  const { data: ukbs } = useQuery({
    queryKey: ["userKbs", kbUser?.userId], queryFn: () => userKbs(kbUser!.userId), enabled: !!kbUser,
  });

  const invalidate = () => { qc.invalidateQueries({ queryKey: ["users"] }); qc.invalidateQueries({ queryKey: ["departments"] }); };

  const saveMut = useMutation({
    mutationFn: async (v: any) =>
      editing ? updateUser(editing.userId, { name: v.name, department: v.department, role: v.role })
              : createUser(v),
    onSuccess: (d: any) => {
      if (editing) { message.success("已更新"); setOpen(false); }
      else { setNewKey(d.apiKey); setOpen(false); form.resetFields(); message.success("已创建成员"); }
      invalidate();
    },
  });
  const delMut = useMutation({ mutationFn: deleteUser, onSuccess: () => { message.success("已移除"); invalidate(); } });
  const grantMut = useMutation({
    mutationFn: () => bulkGrant(kbUser!.userId, grantKbs, grantRole),
    onSuccess: (d) => { message.success(`已授权 ${d.granted} 个库`); setGrantKbs([]); qc.invalidateQueries({ queryKey: ["userKbs", kbUser?.userId] }); },
  });
  const revokeMut = useMutation({
    mutationFn: (kbId: string) => revoke(kbId, kbUser!.userId),
    onSuccess: () => { message.success("已撤销"); qc.invalidateQueries({ queryKey: ["userKbs", kbUser?.userId] }); },
  });

  const openCreate = () => { setEditing(null); form.resetFields(); form.setFieldsValue({ role: "viewer" }); setOpen(true); };
  const openEdit = (m: Member) => { setEditing(m); form.setFieldsValue({ externalId: m.externalId, name: m.name, department: m.department, role: m.role }); setOpen(true); };
  const openKbs = (m: Member) => { setKbUser(m); setGrantKbs([]); setGrantRole("viewer"); };

  return (
    <>
      <Space style={{ justifyContent: "space-between", width: "100%", marginBottom: 12 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>成员管理（admin）</Typography.Title>
        <Button type="primary" icon={<UserAddOutlined />} onClick={openCreate}>新增成员</Button>
      </Space>
      <Space style={{ marginBottom: 12 }}>
        <span style={{ fontSize: 13, color: "#8aa0c0" }}>部门筛选</span>
        <Select style={{ width: 180 }} allowClear placeholder="全部部门" value={dept} onChange={setDept}
          options={(depts || []).map((d) => ({ value: d, label: d }))} />
        <Button icon={<ReloadOutlined />} onClick={() => invalidate()}>刷新</Button>
      </Space>

      <Table<Member>
        rowKey="userId"
        loading={isLoading}
        dataSource={users}
        columns={[
          { title: "账号", render: (_: unknown, r: Member) => <div>{r.name || r.externalId}<br /><Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.externalId}</Typography.Text></div> },
          { title: "部门", dataIndex: "department", width: 120, render: (d) => d ? <Tag color="cyan">{d}</Tag> : <Typography.Text type="secondary">—</Typography.Text> },
          { title: "角色", dataIndex: "role", width: 90, render: (r: string) => <Tag color={ROLE_COLOR[r] || "default"}>{r}</Tag> },
          { title: "可见库数", dataIndex: "kbCount", width: 90 },
          {
            title: "操作", width: 260,
            render: (_: unknown, r: Member) => (
              <Space>
                <Button size="small" type="primary" onClick={() => openKbs(r)}>管理可见库</Button>
                <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
                {r.role !== "owner" && (
                  <Popconfirm title={`移除成员 ${r.externalId}？`} onConfirm={() => delMut.mutate(r.userId)}>
                    <Button size="small" danger>移除</Button>
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]}
      />

      {/* 新增 / 编辑成员 */}
      <Modal
        title={editing ? "编辑成员" : "新增成员"}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.validateFields().then((v) => saveMut.mutate(v))}
        confirmLoading={saveMut.isPending}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="externalId" label="登录账号 / externalId" rules={[{ required: true }]}>
            <Input placeholder="如 zhangsan 或邮箱" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="name" label="显示名"><Input placeholder="张三" /></Form.Item>
          <Form.Item name="department" label="部门"><Input placeholder="如 研发 / 财务" /></Form.Item>
          <Form.Item name="role" label="租户角色" rules={[{ required: true }]}>
            <Select options={[
              { value: "viewer", label: "viewer（仅显式授权的库）" },
              { value: "editor", label: "editor（+ team/tenant 库可读）" },
              { value: "admin", label: "admin（全部库，可授权）" },
              { value: "owner", label: "owner（超管，慎用）" },
            ]} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 新建成员的 apiKey（仅一次）*/}
      <Modal title="成员已创建" open={!!newKey} onCancel={() => setNewKey(null)} onOk={() => setNewKey(null)} okText="我已保存">
        <Alert type="warning" showIcon message="API Key 仅此一次显示，请立即复制交给成员。" style={{ marginBottom: 12 }} />
        <Typography.Paragraph copyable code style={{ background: "rgba(148,163,184,0.12)", padding: 8, borderRadius: 6 }}>
          {newKey || ""}
        </Typography.Paragraph>
      </Modal>

      {/* 管理可见库 */}
      <Drawer
        title={`可见库管理 · ${kbUser?.name || kbUser?.externalId || ""}`}
        open={!!kbUser} onClose={() => setKbUser(null)} width={460}
      >
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          该成员当前可访问的库（"角色/可见性"为租户角色自动派生，不可撤销；"授权"为显式授权，可撤销）。
        </Typography.Text>
        <Table
          size="small" rowKey="kbId" style={{ marginTop: 12 }}
          dataSource={ukbs} pagination={false}
          columns={[
            { title: "知识库", dataIndex: "name" },
            { title: "来源", dataIndex: "source", width: 110, render: (s: string) => <Tag color={s.startsWith("授权") ? "gold" : "blue"}>{s}</Tag> },
            { title: "角色", dataIndex: "role", width: 80 },
            {
              title: "", width: 70,
              render: (_: unknown, r: any) => r.canRevoke
                ? <Popconfirm title="撤销该库访问？" onConfirm={() => revokeMut.mutate(r.kbId)}><Button size="small" danger>撤销</Button></Popconfirm>
                : <Typography.Text type="secondary" style={{ fontSize: 11 }}>—</Typography.Text>,
            },
          ]}
        />
        <div style={{ marginTop: 16, padding: 12, background: "rgba(148,163,184,0.08)", borderRadius: 8 }}>
          <Typography.Text strong>批量授权更多库</Typography.Text>
          <div style={{ marginTop: 8 }}>
            <Select mode="multiple" style={{ width: "100%" }} maxTagCount="responsive" placeholder="选择要授权的知识库"
              value={grantKbs} onChange={setGrantKbs}
              options={(kbs || []).map((k) => ({ value: k.id, label: k.name }))} />
          </div>
          <Space style={{ marginTop: 8 }}>
            <Select style={{ width: 130 }} value={grantRole} onChange={setGrantRole}
              options={[{ value: "viewer" }, { value: "editor" }, { value: "admin" }]} />
            <Button type="primary" disabled={grantKbs.length === 0} loading={grantMut.isPending} onClick={() => grantMut.mutate()}>
              授权 {grantKbs.length || ""}
            </Button>
          </Space>
        </div>
      </Drawer>
    </>
  );
}
