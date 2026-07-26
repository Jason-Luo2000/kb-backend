import { useState } from "react";
import { Table, Button, Space, Tag, Typography, Modal, Form, Input, Select, Drawer, Popconfirm, message, Alert, Divider } from "antd";
import { UserAddOutlined, ReloadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listUsers, listDepartments, listGroups, createUser, updateUser, deleteUser, userKbs, bulkGrant, revoke, grantBulk, listKbs,
} from "../api";
import type { Member } from "../types";
import { usePageSize, paginationProps } from "../usePageSize";

const ROLE_COLOR: Record<string, string> = { owner: "red", admin: "gold", editor: "blue", viewer: "default" };

export default function Members() {
  const qc = useQueryClient();
  const [dept, setDept] = useState<string | undefined>();
  const [group, setGroup] = useState<string | undefined>();
  const { data: users, isLoading } = useQuery({ queryKey: ["users", dept, group], queryFn: () => listUsers(dept, group) });
  const { data: depts } = useQuery({ queryKey: ["departments"], queryFn: listDepartments });
  const { data: groups } = useQuery({ queryKey: ["groups"], queryFn: listGroups });
  const { data: kbs } = useQuery({ queryKey: ["kbs"], queryFn: listKbs });
  // 全部成员（供批量授权的成员选择器；不受筛选影响）
  const { data: allUsers } = useQuery({ queryKey: ["users", undefined, undefined], queryFn: () => listUsers() });

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

  // 批量授权
  const [batchOpen, setBatchOpen] = useState(false);
  const [bKbs, setBKbs] = useState<string[]>([]);
  const [bRole, setBRole] = useState("viewer");
  const [bDept, setBDept] = useState<string | undefined>();
  const [bGroup, setBGroup] = useState<string | undefined>();
  const [bUids, setBUids] = useState<string[]>([]);
  const [preview, setPreview] = useState<{ userId: string; externalId: string; name?: string | null }[] | null>(null);
  const [pageSize, setPageSize] = usePageSize();

  // 选择式批量管理（编辑属性 / 删除）
  const [selected, setSelected] = useState<string[]>([]);
  const [editOpen, setEditOpen] = useState(false);
  const [editForm] = Form.useForm();

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["users"] });
    qc.invalidateQueries({ queryKey: ["departments"] });
    qc.invalidateQueries({ queryKey: ["groups"] });
  };

  const saveMut = useMutation({
    mutationFn: async (v: any) =>
      editing ? updateUser(editing.userId, { name: v.name, department: v.department, group: v.group, role: v.role })
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
  const previewMut = useMutation({
    mutationFn: () => grantBulk({ kbIds: bKbs, role: bRole, department: bDept, group: bGroup, userIds: bUids, dryRun: true }),
    onSuccess: (d) => setPreview(d.users),
  });
  const batchMut = useMutation({
    mutationFn: () => grantBulk({ kbIds: bKbs, role: bRole, department: bDept, group: bGroup, userIds: bUids }),
    onSuccess: (d) => { message.success(`已授权 ${d.granted} 人`); setBatchOpen(false); setPreview(null); invalidate(); },
  });
  // 批量编辑属性（部门/小组/角色）+ 批量删除：逐个 PATCH/DELETE
  const batchUpdate = useMutation({
    mutationFn: async (body: { department?: string; group?: string; role?: string }) =>
      Promise.all(selected.map((id) => updateUser(id, body))),
    onSuccess: () => { message.success(`已更新 ${selected.length} 人`); setEditOpen(false); setSelected([]); invalidate(); },
  });
  const batchDelete = useMutation({
    mutationFn: async () => {
      const results = await Promise.allSettled(selected.map((id) => deleteUser(id)));
      return { ok: results.filter((r) => r.status === "fulfilled").length, total: selected.length };
    },
    onSuccess: (r) => {
      message.success(`已移除 ${r.ok}/${r.total}${r.ok < r.total ? "（其余失败：可能含不可删的管理员）" : ""}`);
      setSelected([]); invalidate();
    },
  });

  const openCreate = () => { setEditing(null); form.resetFields(); form.setFieldsValue({ role: "viewer" }); setOpen(true); };
  const openEdit = (m: Member) => { setEditing(m); form.setFieldsValue({ externalId: m.externalId, name: m.name, department: m.department, group: m.groupName, role: m.role }); setOpen(true); };
  const openKbs = (m: Member) => { setKbUser(m); setGrantKbs([]); setGrantRole("viewer"); };
  const openBatch = () => { setBatchOpen(true); setBKbs([]); setBRole("viewer"); setBDept(undefined); setBGroup(undefined); setBUids([]); setPreview(null); };

  return (
    <>
      <Space style={{ justifyContent: "space-between", width: "100%", marginBottom: 12 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>成员管理（admin）</Typography.Title>
        <Space>
          <Button icon={<ThunderboltOutlined />} onClick={openBatch}>批量授权</Button>
          <Button type="primary" icon={<UserAddOutlined />} onClick={openCreate}>新增成员</Button>
        </Space>
      </Space>
      <Space style={{ marginBottom: 12 }}>
        <span style={{ fontSize: 13, color: "#8aa0c0" }}>部门</span>
        <Select style={{ width: 160 }} allowClear placeholder="全部" value={dept} onChange={setDept}
          options={(depts || []).map((d) => ({ value: d, label: d }))} />
        <span style={{ fontSize: 13, color: "#8aa0c0" }}>小组</span>
        <Select style={{ width: 160 }} allowClear placeholder="全部" value={group} onChange={setGroup}
          options={(groups || []).map((g) => ({ value: g, label: g }))} />
        <Button icon={<ReloadOutlined />} onClick={() => invalidate()}>刷新</Button>
      </Space>

      {selected.length > 0 && (
        <Space style={{ marginBottom: 12 }}>
          <Typography.Text type="secondary">已选 {selected.length} 人</Typography.Text>
          <Button onClick={() => { editForm.resetFields(); setEditOpen(true); }}>批量编辑（部门/小组/角色）</Button>
          <Popconfirm title={`移除 ${selected.length} 个成员？`} onConfirm={() => batchDelete.mutate()}>
            <Button danger loading={batchDelete.isPending}>批量移除</Button>
          </Popconfirm>
          <Button onClick={() => setSelected([])}>取消选择</Button>
        </Space>
      )}

      <Table<Member>
        rowKey="userId"
        loading={isLoading}
        dataSource={users}
        pagination={paginationProps(pageSize, setPageSize)}
        rowSelection={{
          selectedRowKeys: selected,
          onChange: (keys) => setSelected(keys.map(String)),
          getCheckboxProps: (r: Member) => ({ disabled: r.role === "owner" }),  // owner（含自己）不可选入批量
        }}
        columns={[
          { title: "账号", render: (_: unknown, r: Member) => <div>{r.name || r.externalId}<br /><Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.externalId}</Typography.Text></div> },
          { title: "部门", dataIndex: "department", width: 110, render: (d) => d ? <Tag color="cyan">{d}</Tag> : <Typography.Text type="secondary">—</Typography.Text> },
          { title: "小组", dataIndex: "groupName", width: 110, render: (d) => d ? <Tag color="geekblue">{d}</Tag> : <Typography.Text type="secondary">—</Typography.Text> },
          { title: "角色", dataIndex: "role", width: 80, render: (r: string) => <Tag color={ROLE_COLOR[r] || "default"}>{r}</Tag> },
          { title: "可见库", dataIndex: "kbCount", width: 70 },
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
      <Modal title={editing ? "编辑成员" : "新增成员"} open={open} onCancel={() => setOpen(false)}
        onOk={() => form.validateFields().then((v) => saveMut.mutate(v))} confirmLoading={saveMut.isPending}>
        <Form form={form} layout="vertical">
          <Form.Item name="externalId" label="登录账号 / externalId" rules={[{ required: true }]}>
            <Input placeholder="如 zhangsan 或邮箱" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="name" label="显示名"><Input placeholder="张三" /></Form.Item>
          <Form.Item name="department" label="部门"><Input placeholder="如 研发 / 财务" /></Form.Item>
          <Form.Item name="group" label="小组"><Input placeholder="如 前端 / 审计一组" /></Form.Item>
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

      {/* 新建成员 apiKey（仅一次）*/}
      <Modal title="成员已创建" open={!!newKey} onCancel={() => setNewKey(null)} onOk={() => setNewKey(null)} okText="我已保存">
        <Alert type="warning" showIcon message="API Key 仅此一次显示，请立即复制交给成员。" style={{ marginBottom: 12 }} />
        <Typography.Paragraph copyable code style={{ background: "rgba(148,163,184,0.12)", padding: 8, borderRadius: 6 }}>
          {newKey || ""}
        </Typography.Paragraph>
      </Modal>

      {/* 管理可见库（单人）*/}
      <Drawer title={`可见库管理 · ${kbUser?.name || kbUser?.externalId || ""}`} open={!!kbUser} onClose={() => setKbUser(null)} width={460}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          该成员当前可访问的库（"角色/可见性"为租户角色自动派生，不可撤销；"授权"为显式授权，可撤销）。
        </Typography.Text>
        <Table size="small" rowKey="kbId" style={{ marginTop: 12 }} dataSource={ukbs} pagination={false}
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
          ]} />
        <div style={{ marginTop: 16, padding: 12, background: "rgba(148,163,184,0.08)", borderRadius: 8 }}>
          <Typography.Text strong>授权更多库</Typography.Text>
          <div style={{ marginTop: 8 }}>
            <Select mode="multiple" style={{ width: "100%" }} maxTagCount="responsive" placeholder="选择要授权的知识库"
              value={grantKbs} onChange={setGrantKbs} options={(kbs || []).map((k) => ({ value: k.id, label: k.name }))} />
          </div>
          <Space style={{ marginTop: 8 }}>
            <Select style={{ width: 130 }} value={grantRole} onChange={setGrantRole} options={[{ value: "viewer" }, { value: "editor" }, { value: "admin" }]} />
            <Button type="primary" disabled={grantKbs.length === 0} loading={grantMut.isPending} onClick={() => grantMut.mutate()}>授权 {grantKbs.length || ""}</Button>
          </Space>
        </div>
      </Drawer>

      {/* 批量授权（部门/小组/成员 多维度）*/}
      <Modal title="批量授权可见库" open={batchOpen} onCancel={() => setBatchOpen(false)} width={560} destroyOnClose
        footer={[
          <Button key="cancel" onClick={() => setBatchOpen(false)}>取消</Button>,
          <Button key="preview" icon={<ThunderboltOutlined />} disabled={bKbs.length === 0} loading={previewMut.isPending}
            onClick={() => previewMut.mutate()}>预览匹配</Button>,
          <Button key="ok" type="primary" disabled={!preview || preview.length === 0} loading={batchMut.isPending}
            onClick={() => batchMut.mutate()}>确认授权{preview ? `（${preview.length} 人）` : ""}</Button>,
        ]}>
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="目标范围 = (部门 AND 小组) 过滤 ∪ 显式成员。先「预览匹配」确认人数，再「确认授权」。" />
        <div style={{ marginBottom: 8 }}>
          <Typography.Text strong>选择知识库</Typography.Text>
          <Select mode="multiple" style={{ width: "100%", marginTop: 6 }} maxTagCount="responsive" placeholder="选择要授权的知识库"
            value={bKbs} onChange={setBKbs} options={(kbs || []).map((k) => ({ value: k.id, label: k.name }))} />
        </div>
        <Space style={{ width: "100%", marginBottom: 8 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12, color: "#8aa0c0" }}>部门</div>
            <Select style={{ width: 200 }} allowClear placeholder="按部门" value={bDept} onChange={setBDept}
              options={(depts || []).map((d) => ({ value: d, label: d }))} />
          </div>
          <div>
            <div style={{ fontSize: 12, color: "#8aa0c0" }}>小组</div>
            <Select style={{ width: 200 }} allowClear placeholder="按小组" value={bGroup} onChange={setBGroup}
              options={(groups || []).map((g) => ({ value: g, label: g }))} />
          </div>
        </Space>
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 12, color: "#8aa0c0" }}>或/且 显式选成员</div>
          <Select mode="multiple" style={{ width: "100%" }} maxTagCount="responsive" placeholder="勾选具体成员（与部门/小组并集）"
            value={bUids} onChange={setBUids}
            options={(allUsers || []).map((u) => ({ value: u.userId, label: `${u.name || u.externalId}${u.department ? " · " + u.department : ""}` }))} />
        </div>
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 12, color: "#8aa0c0" }}>授权角色</div>
          <Select style={{ width: 160 }} value={bRole} onChange={setBRole} options={[{ value: "viewer" }, { value: "editor" }, { value: "admin" }]} />
        </div>
        {preview && (
          <>
            <Divider style={{ margin: "10px 0" }} />
            <Typography.Text type="secondary">匹配 {preview.length} 人：</Typography.Text>
            <div style={{ marginTop: 4, maxHeight: 120, overflow: "auto" }}>
              {preview.map((u) => <Tag key={u.userId}>{u.name || u.externalId}</Tag>)}
            </div>
          </>
        )}
      </Modal>

      {/* 批量编辑属性（部门/小组/角色，仅更新填写项）*/}
      <Modal
        title={`批量编辑（${selected.length} 人）`}
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => editForm.validateFields().then((v) => {
          const body: { department?: string; group?: string; role?: string } = {};
          if (v.department) body.department = v.department;
          if (v.group) body.group = v.group;
          if (v.role) body.role = v.role;
          if (!body.department && !body.group && !body.role) { message.warning("未填写任何字段"); return; }
          batchUpdate.mutate(body);
        })}
        confirmLoading={batchUpdate.isPending}
        okText={`应用到 ${selected.length} 人`}
      >
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message="仅更新你填写的字段，留空 / 选「不改」表示不改动。" />
        <Form form={editForm} layout="vertical">
          <Form.Item name="department" label="部门"><Input placeholder="不改" /></Form.Item>
          <Form.Item name="group" label="小组"><Input placeholder="不改" /></Form.Item>
          <Form.Item name="role" label="租户角色">
            <Select options={[{ value: "", label: "不改" }, { value: "viewer" }, { value: "editor" }, { value: "admin" }]} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
