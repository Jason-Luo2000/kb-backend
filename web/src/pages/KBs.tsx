import { useState } from "react";
import { Table, Button, Modal, Form, Input, Select, Space, Tag, Typography, message } from "antd";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { listKbs, createKb, updateKb } from "../api";
import type { KB, ParserConfig } from "../types";
import ParserConfigFields from "../components/ParserConfigFields";

export default function KBs() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const { data, isLoading } = useQuery({ queryKey: ["kbs"], queryFn: listKbs });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<KB | null>(null);
  const [form] = Form.useForm();

  const create = useMutation({
    mutationFn: (v: { name: string; description?: string; visibility?: string; parserConfig?: ParserConfig }) =>
      createKb(v),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kbs"] });
      setOpen(false);
      form.resetFields();
    },
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<KB> }) => updateKb(id, body),
    onSuccess: () => {
      message.success("已更新");
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["kbs"] });
    },
  });

  const openEdit = (r: KB) => {
    setEditing(r);
    form.setFieldsValue({
      name: r.name,
      description: r.description,
      visibility: r.visibility,
      parserConfig: r.parserConfig || { method: "naive" },
    });
  };

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>知识库</Typography.Title>
        <Button type="primary" onClick={() => { setEditing(null); form.resetFields(); setOpen(true); }}>新建</Button>
      </Space>
      <Table<KB>
        rowKey="id"
        loading={isLoading}
        dataSource={data}
        onRow={(r) => ({ onClick: () => nav(`/kbs/${r.id}/docs`), style: { cursor: "pointer" } })}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "描述", dataIndex: "description", ellipsis: true },
          { title: "文档数", dataIndex: "docCount", width: 90 },
          { title: "分块方法", width: 130, render: (_: unknown, r: KB) => <Tag>{r.parserConfig?.method || "naive"}</Tag> },
          { title: "角色", dataIndex: "role", width: 100, render: (r) => <Tag>{r}</Tag> },
          { title: "可见性", dataIndex: "visibility", width: 100 },
          {
            title: "操作", width: 90,
            render: (_: unknown, r: KB) => (
              <Button size="small" onClick={(e) => { e.stopPropagation(); openEdit(r); }}>配置</Button>
            ),
          },
        ]}
      />

      {/* 新建 */}
      <Modal
        title="新建知识库"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={create.isPending}
        width={640}
      >
        <Form form={form} layout="vertical" onFinish={(v) => create.mutate(v)} initialValues={{ visibility: "team" }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="description" label="描述"><Input /></Form.Item>
          <Form.Item name="visibility" label="可见性">
            <Select options={[{ value: "me" }, { value: "team" }, { value: "tenant" }]} />
          </Form.Item>
          <Form.Item label="分块配置（该库默认）">
            <Form.Item name="parserConfig" valuePropName="value" trigger="onChange" noStyle>
              <ParserConfigFields />
            </Form.Item>
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑配置 */}
      <Modal
        title={`配置 · ${editing?.name || ""}`}
        open={!!editing}
        onCancel={() => setEditing(null)}
        onOk={() =>
          form.validateFields().then((v) =>
            update.mutate({ id: editing!.id, body: { name: v.name, description: v.description, visibility: v.visibility, parserConfig: v.parserConfig } })
          )
        }
        confirmLoading={update.isPending}
        width={640}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="description" label="描述"><Input /></Form.Item>
          <Form.Item name="visibility" label="可见性">
            <Select options={[{ value: "me" }, { value: "team" }, { value: "tenant" }]} />
          </Form.Item>
          <Form.Item label="分块配置（改后对新上传生效；已入库文档需重新解析）">
            <Form.Item name="parserConfig" valuePropName="value" trigger="onChange" noStyle>
              <ParserConfigFields />
            </Form.Item>
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
