import { useState } from "react";
import { Table, Button, Modal, Form, Input, Select, Space, Tag, Typography, Popconfirm, message } from "antd";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { listKbs, createKb, updateKb, deleteKb } from "../api";
import type { KB, ParserConfig } from "../types";
import ParserConfigFields from "../components/ParserConfigFields";
import { usePageSize, paginationProps } from "../usePageSize";

export default function KBs() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const { data, isLoading } = useQuery({ queryKey: ["kbs"], queryFn: listKbs });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<KB | null>(null);
  const [form] = Form.useForm();
  const [selected, setSelected] = useState<string[]>([]);
  const [pageSize, setPageSize] = usePageSize();

  // 批量配置
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchCfg, setBatchCfg] = useState<ParserConfig>({ method: "naive" });
  const [batchVis, setBatchVis] = useState<string>("team");

  const create = useMutation({
    mutationFn: (v: { name: string; description?: string; visibility?: string; parserConfig?: ParserConfig }) => createKb(v),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["kbs"] }); setOpen(false); form.resetFields(); },
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<KB> }) => updateKb(id, body),
    onSuccess: () => { message.success("已更新"); setEditing(null); qc.invalidateQueries({ queryKey: ["kbs"] }); },
  });
  const delOne = useMutation({
    mutationFn: deleteKb,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["kbs"] }); },
  });
  // 批量应用配置（分块 + 可见性）到所有选中库
  const batchApply = useMutation({
    mutationFn: async () => Promise.all(selected.map((id) => updateKb(id, { parserConfig: batchCfg, visibility: batchVis }))),
    onSuccess: () => { message.success(`已应用到 ${selected.length} 个库`); setBatchOpen(false); setSelected([]); qc.invalidateQueries({ queryKey: ["kbs"] }); },
  });
  const batchDelete = useMutation({
    mutationFn: async () => Promise.all(selected.map((id) => deleteKb(id))),
    onSuccess: () => { message.success(`已删除 ${selected.length} 个库`); setSelected([]); qc.invalidateQueries({ queryKey: ["kbs"] }); },
  });

  const openEdit = (r: KB) => {
    setEditing(r);
    form.setFieldsValue({
      name: r.name, description: r.description, visibility: r.visibility,
      parserConfig: r.parserConfig || { method: "naive" },
    });
  };

  return (
    <>
      <Space style={{ justifyContent: "space-between", width: "100%", marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>知识库</Typography.Title>
        <Button type="primary" onClick={() => { setEditing(null); form.resetFields(); setOpen(true); }}>新建</Button>
      </Space>

      {selected.length > 0 && (
        <Space style={{ marginBottom: 12 }}>
          <Typography.Text type="secondary">已选 {selected.length} 个</Typography.Text>
          <Button onClick={() => { setBatchCfg({ method: "naive" }); setBatchVis("team"); setBatchOpen(true); }}>
            批量配置（分块/可见性）
          </Button>
          <Popconfirm title={`删除 ${selected.length} 个知识库？（文档保留在个人库）`} onConfirm={() => batchDelete.mutate()}>
            <Button danger loading={batchDelete.isPending}>批量删除</Button>
          </Popconfirm>
          <Button onClick={() => setSelected([])}>取消选择</Button>
        </Space>
      )}

      <Table<KB>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        pagination={paginationProps(pageSize, setPageSize)}
        rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys.map(String)) }}
        onRow={(r) => ({ onClick: () => nav(`/kbs/${r.id}/docs`), style: { cursor: "pointer" } })}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "描述", dataIndex: "description", ellipsis: true },
          { title: "文档数", dataIndex: "docCount", width: 80 },
          { title: "分块方法", width: 120, render: (_: unknown, r: KB) => <Tag>{r.parserConfig?.method || "naive"}</Tag> },
          { title: "可见性", dataIndex: "visibility", width: 90 },
          { title: "角色", dataIndex: "role", width: 90, render: (r) => <Tag>{r}</Tag> },
          {
            title: "操作", width: 130,
            render: (_: unknown, r: KB) => (
              <Button size="small" onClick={(e) => { e.stopPropagation(); openEdit(r); }}>配置</Button>
            ),
          },
        ]}
      />

      {/* 新建 / 编辑单个 */}
      <Modal
        title={editing ? "编辑知识库" : "新建知识库"}
        open={editing ? !!editing : open}
        onCancel={() => { setOpen(false); setEditing(null); }}
        onOk={() => {
          if (editing) {
            form.validateFields().then((v) => update.mutate({ id: editing.id, body: { name: v.name, description: v.description, visibility: v.visibility, parserConfig: v.parserConfig } }));
          } else {
            form.submit();
          }
        }}
        confirmLoading={create.isPending || update.isPending}
        width={640}
      >
        <Form form={form} layout="vertical" onFinish={(v) => create.mutate(v)} initialValues={{ visibility: "team" }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="description" label="描述"><Input /></Form.Item>
          <Form.Item name="visibility" label="可见性">
            <Select options={[{ value: "me" }, { value: "team" }, { value: "tenant" }]} />
          </Form.Item>
          <Form.Item label="分块配置">
            <Form.Item name="parserConfig" valuePropName="value" trigger="onChange" noStyle>
              <ParserConfigFields />
            </Form.Item>
          </Form.Item>
        </Form>
      </Modal>

      {/* 批量配置（应用到所有选中库）*/}
      <Modal
        title={`批量配置（${selected.length} 个知识库）`}
        open={batchOpen}
        onCancel={() => setBatchOpen(false)}
        onOk={() => batchApply.mutate()}
        confirmLoading={batchApply.isPending}
        okText={`应用到 ${selected.length} 个库`}
        width={640}
      >
        <Typography.Text type="secondary">所选配置将覆盖这些库的分块方法/参数 + 可见性（已入库文档不受影响，需在文档页重新解析）。</Typography.Text>
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 12, marginBottom: 4, color: "#8aa0c0" }}>可见性</div>
          <Select style={{ width: 160 }} value={batchVis} onChange={setBatchVis}
            options={[{ value: "me", label: "me（仅自己）" }, { value: "team", label: "team（团队）" }, { value: "tenant", label: "tenant（全租户）" }]} />
        </div>
        <div style={{ fontSize: 12, margin: "12px 0 4px", color: "#8aa0c0" }}>分块配置</div>
        <ParserConfigFields value={batchCfg} onChange={setBatchCfg} />
      </Modal>
    </>
  );
}
