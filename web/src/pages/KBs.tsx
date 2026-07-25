import { useState } from "react";
import { Table, Button, Modal, Form, Input, Select, Space, Tag, Typography } from "antd";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { listKbs, createKb } from "../api";
import type { KB } from "../types";

export default function KBs() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const { data, isLoading } = useQuery({ queryKey: ["kbs"], queryFn: listKbs });
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const create = useMutation({
    mutationFn: (v: { name: string; description?: string; visibility?: string }) =>
      createKb(v.name, v.description, v.visibility),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kbs"] });
      setOpen(false);
      form.resetFields();
    },
  });

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>知识库</Typography.Title>
        <Button type="primary" onClick={() => setOpen(true)}>新建</Button>
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
          { title: "角色", dataIndex: "role", width: 100, render: (r) => <Tag>{r}</Tag> },
          { title: "可见性", dataIndex: "visibility", width: 100 },
        ]}
      />
      <Modal
        title="新建知识库"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={create.isPending}
      >
        <Form form={form} layout="vertical" onFinish={(v) => create.mutate(v)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="description" label="描述"><Input /></Form.Item>
          <Form.Item name="visibility" label="可见性" initialValue="team">
            <Select options={[{ value: "me" }, { value: "team" }, { value: "tenant" }]} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
