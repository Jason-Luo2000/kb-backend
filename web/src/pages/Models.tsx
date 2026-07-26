import { useEffect, useState } from "react";
import {
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createModel,
  deleteModel,
  getModelDefaults,
  listModels,
  testModel,
  updateModel,
  type ModelInput,
} from "../api";
import type { ModelConfig, ModelKind, ProviderType } from "../types";

const KIND_LABEL: Record<ModelKind, string> = { llm: "LLM 对话", embedding: "向量嵌入", rerank: "重排" };
const PROVIDERS: { value: ProviderType; label: string }[] = [
  { value: "openai", label: "OpenAI 兼容（OpenAI / DeepSeek / Moonshot / vLLM …）" },
  { value: "anthropic", label: "Anthropic（含智谱 glm anthropic 端点）" },
  { value: "zhipu", label: "智谱（OpenAI 兼容端点）" },
  { value: "local", label: "本地（Ollama / vLLM，OpenAI 兼容）" },
  { value: "gemini", label: "Gemini（需代理，暂未适配）" },
];

export default function Models() {
  const qc = useQueryClient();
  const { data: models } = useQuery({ queryKey: ["models"], queryFn: listModels });
  const { data: defaults } = useQuery({ queryKey: ["modelDefaults"], queryFn: getModelDefaults });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ModelConfig | null>(null);
  const [detail, setDetail] = useState<ModelConfig | null>(null);
  const [form] = Form.useForm<ModelInput>();

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["models"] });
    qc.invalidateQueries({ queryKey: ["modelDefaults"] });
  };

  const saveMut = useMutation({
    mutationFn: async (v: ModelInput) => {
      const body: ModelInput = {
        ...v,
        dim: v.kind === "embedding" ? v.dim : null,
        maxTokens: v.kind === "llm" ? v.maxTokens : null,
      };
      if (editing) {
        // 空 apiKey 表示不改 key
        return updateModel(editing.id, body.apiKey ? body : { ...body, apiKey: undefined });
      }
      return createModel(body);
    },
    onSuccess: () => {
      message.success(editing ? "已更新" : "已创建");
      setOpen(false);
      invalidate();
    },
  });

  const delMut = useMutation({
    mutationFn: deleteModel,
    onSuccess: () => {
      message.success("已删除");
      invalidate();
    },
  });

  const testMut = useMutation({
    mutationFn: testModel,
    onSuccess: (d) => (d.ok ? message.success(`连通正常：${d.detail}`) : message.error(`失败：${d.detail}`)),
  });

  const openCreate = () => {
    setEditing(null);
    setOpen(true);
  };
  const openEdit = (m: ModelConfig) => {
    setEditing(m);
    setOpen(true);
  };

  // 弹窗打开后再回填表单（编辑复用现有配置；新增给默认值）。避免 destroyOnClose 时机错位
  useEffect(() => {
    if (!open) return;
    if (editing) {
      form.setFieldsValue({
        name: editing.name,
        kind: editing.kind,
        providerType: editing.providerType,
        baseUrl: editing.baseUrl,
        modelName: editing.modelName,
        dim: editing.dim,
        maxTokens: editing.maxTokens,
        isDefault: editing.isDefault,
        apiKey: "", // 留空 = 不改 key
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ kind: "llm", providerType: "openai", isDefault: true });
    }
  }, [open, editing, form]);

  return (
    <>
      <Typography.Title level={3}>模型配置</Typography.Title>
      <Typography.Text type="secondary">
        LLM / 嵌入 / 重排 三类 provider，运行时按租户默认生效（系统内置为 env 兜底，可被租户行覆盖）。API-key 加密存库。
      </Typography.Text>

      <Card size="small" style={{ marginTop: 12, marginBottom: 16 }}>
        <Space size="large">
          {(Object.keys(KIND_LABEL) as ModelKind[]).map((k) => {
            const d = defaults?.[k];
            return (
              <span key={k}>
                <Tag color="blue">{KIND_LABEL[k]}默认</Tag>
                {d ? `${d.modelName} · ${d.providerType}` : <Typography.Text type="secondary">未配置</Typography.Text>}
              </span>
            );
          })}
        </Space>
      </Card>

      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" onClick={openCreate}>
          新增模型
        </Button>
        <Button onClick={() => invalidate()}>刷新</Button>
      </Space>

      <Table
        rowKey="id"
        dataSource={models}
        pagination={false}
        columns={[
          { title: "名称", dataIndex: "name" },
          {
            title: "类型",
            dataIndex: "kind",
            render: (k: ModelKind) => <Tag>{KIND_LABEL[k]}</Tag>,
            filters: (Object.keys(KIND_LABEL) as ModelKind[]).map((k) => ({ text: KIND_LABEL[k], value: k })),
            onFilter: (v, r: ModelConfig) => r.kind === v,
          },
          {
            title: "默认",
            dataIndex: "isDefault",
            width: 80,
            render: (d: boolean) => (d ? <Tag color="green">默认</Tag> : null),
          },
          {
            title: "来源",
            dataIndex: "system",
            width: 80,
            render: (s: boolean) => (s ? <Tag>系统</Tag> : <Tag color="purple">租户</Tag>),
          },
          {
            title: "操作",
            width: 210,
            render: (_: unknown, r: ModelConfig) => (
              <Space>
                <Button size="small" type="primary" onClick={() => setDetail(r)}>详情</Button>
                <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
                {!r.system && (
                  <Popconfirm title="删除该模型？" onConfirm={() => delMut.mutate(r.id)}>
                    <Button size="small" danger>删除</Button>
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title={editing ? "编辑模型" : "新增模型"}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.validateFields().then((v) => saveMut.mutate(v))}
        confirmLoading={saveMut.isPending}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="显示名称" rules={[{ required: true }]}>
            <Input placeholder="如 zhipu-glm" />
          </Form.Item>
          <Form.Item name="kind" label="类型" rules={[{ required: true }]}>
            <Select
              disabled={!!editing}
              options={(Object.keys(KIND_LABEL) as ModelKind[]).map((k) => ({ value: k, label: KIND_LABEL[k] }))}
            />
          </Form.Item>
          <Form.Item name="providerType" label="Provider" rules={[{ required: true }]}>
            <Select options={PROVIDERS} />
          </Form.Item>
          <Form.Item name="baseUrl" label="Base URL" tooltip="OpenAI 兼容填到 /v1；Anthropic 填到 /api/anthropic">
            <Input placeholder="https://api.openai.com/v1" />
          </Form.Item>
          <Form.Item name="modelName" label="模型名" rules={[{ required: true }]}>
            <Input placeholder="如 gpt-4o / embedding-3 / bge-reranker-v2-m3" />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, cur) => prev.kind !== cur.kind}>
            {({ getFieldValue }) =>
              getFieldValue("kind") === "embedding" ? (
                <Form.Item name="dim" label="维度">
                  <InputNumber min={1} style={{ width: "100%" }} placeholder="如 2048" />
                </Form.Item>
              ) : null
            }
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, cur) => prev.kind !== cur.kind}>
            {({ getFieldValue }) =>
              getFieldValue("kind") === "llm" ? (
                <Form.Item name="maxTokens" label="最大输出 token" tooltip="该模型每次对话/总结的输出上限；留空用默认 4096">
                  <InputNumber min={1} style={{ width: "100%" }} placeholder="如 4096 / 8192" />
                </Form.Item>
              ) : null
            }
          </Form.Item>
          <Form.Item name="apiKey" label={editing ? "API Key（留空不改）" : "API Key"}>
            <Input.Password placeholder="sk-..." />
          </Form.Item>
          <Form.Item name="isDefault" label="设为该类型默认" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      {/* 详情 */}
      <Modal
        title={`模型详情 · ${detail?.name || ""}`}
        open={!!detail}
        onCancel={() => setDetail(null)}
        footer={[
          <Button key="test" loading={testMut.isPending} onClick={() => detail && testMut.mutate(detail.id)}>
            测连通
          </Button>,
          <Button key="close" type="primary" onClick={() => setDetail(null)}>
            关闭
          </Button>,
        ]}
      >
        {detail && (
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="类型">{KIND_LABEL[detail.kind]}</Descriptions.Item>
            <Descriptions.Item label="Provider">{detail.providerType}</Descriptions.Item>
            <Descriptions.Item label="模型">{detail.modelName}</Descriptions.Item>
            <Descriptions.Item label="Base URL">
              <Typography.Text copyable style={{ wordBreak: "break-all" }}>
                {detail.baseUrl || "-"}
              </Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="API Key">
              {detail.hasKey ? detail.apiKey : <Typography.Text type="secondary">无</Typography.Text>}
            </Descriptions.Item>
            {detail.kind === "embedding" && (
              <Descriptions.Item label="维度">{detail.dim ?? "-"}</Descriptions.Item>
            )}
            {detail.kind === "llm" && (
              <Descriptions.Item label="最大输出 token">{detail.maxTokens ?? "默认 4096"}</Descriptions.Item>
            )}
            <Descriptions.Item label="默认">{detail.isDefault ? "是" : "否"}</Descriptions.Item>
            <Descriptions.Item label="来源">{detail.system ? "系统（env 种子，只读）" : "租户"}</Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </>
  );
}
