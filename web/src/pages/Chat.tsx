import { useState } from "react";
import { Input, Button, Select, Typography, Tag, Space, Card, Drawer, Slider, InputNumber, Switch, Alert, Divider } from "antd";
import { SendOutlined, SettingOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { chat, listChatModels, listKbs } from "../api";
import type { ChatResult } from "../types";
import CitationDrawer from "../components/CitationDrawer";

interface Msg {
  role: "user" | "assistant";
  text?: string;
  result?: ChatResult;
  kbs?: string[]; // 该轮选中的库（空=全部）
}

interface Settings {
  kbIds: string[];
  modelId?: string;
  temperature?: number;
  maxTokens?: number;
  topK: number;
  similarityThreshold?: number;
  rerank?: boolean; // undefined=自动
  mode: string;
  systemPrompt?: string;
  cite: boolean;
}

const DEFAULTS: Settings = { kbIds: [], topK: 8, mode: "hybrid", cite: true };

/** 把答案里的 [n] 转成可点锚链接，指向该消息的来源条目 #ref-{mi}-n */
function linkifyCitations(answer: string, mi: number): string {
  return answer.replace(/\[(\d+)\]/g, (_, n) => `[[${n}]](#ref-${mi}-${n})`);
}

export default function Chat() {
  const [q, setQ] = useState("");
  const [s, setS] = useState<Settings>({ ...DEFAULTS });
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [loading, setLoading] = useState(false);
  const [cfgOpen, setCfgOpen] = useState(false);
  const [cite, setCite] = useState<{ docId: string; anchor: string } | null>(null);
  const { data: kbs } = useQuery({ queryKey: ["kbs"], queryFn: listKbs });
  const { data: chatModels } = useQuery({ queryKey: ["chatModels"], queryFn: listChatModels });
  const set = (patch: Partial<Settings>) => setS((p) => ({ ...p, ...patch }));

  async function ask() {
    if (!q.trim() || loading) return;
    const question = q;
    setQ("");
    setMsgs((m) => [...m, { role: "user", text: question, kbs: s.kbIds }]);
    setLoading(true);
    try {
      const r = await chat({
        query: question,
        knowledgeBaseIds: s.kbIds.length ? s.kbIds : undefined,
        modelId: s.modelId,
        systemPrompt: s.systemPrompt,
        temperature: s.temperature,
        maxTokens: s.maxTokens,
        topK: s.topK,
        similarityThreshold: s.similarityThreshold,
        rerank: s.rerank,
        mode: s.mode,
        cite: s.cite,
      });
      setMsgs((m) => [...m, { role: "assistant", result: r }]);
    } finally {
      setLoading(false);
    }
  }

  const modelName = (chatModels || []).find((m) => m.id === s.modelId)?.name;

  return (
    <>
      <Space style={{ justifyContent: "space-between", width: "100%", marginBottom: 8 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>问答（RAG · 检索 + 生成）</Typography.Title>
        <Button icon={<SettingOutlined />} onClick={() => setCfgOpen(true)}>问答设置</Button>
      </Space>
      {/* 当前生效配置速览（抽屉关闭时也能看到）*/}
      <Space size={6} wrap style={{ marginBottom: 10 }}>
        <Tag color="cyan">库 {s.kbIds.length ? s.kbIds.length : "全部"}</Tag>
        <Tag color="geekblue">{modelName || "默认 LLM"}</Tag>
        <Tag>temp {s.temperature ?? "自动"}</Tag>
        <Tag>top{s.topK}</Tag>
        <Tag>{s.mode}</Tag>
        <Tag color={s.rerank === false ? undefined : "gold"}>{s.rerank === false ? "rerank 关" : s.rerank ? "rerank 开" : "rerank 自动"}</Tag>
        {s.similarityThreshold ? <Tag>阈值 {s.similarityThreshold}</Tag> : null}
      </Space>

      {/* 消息流 */}
      <div style={{ maxHeight: "52vh", overflowY: "auto", marginBottom: 12, paddingRight: 8 }}>
        {msgs.map((m, i) => (
          <Card key={i} size="small" style={{
            marginBottom: 8,
            background: m.role === "user" ? "rgba(34,211,238,0.10)" : "rgba(99,102,241,0.08)",
            borderColor: m.role === "user" ? "rgba(34,211,238,0.35)" : "rgba(99,102,241,0.28)",
          }}>
            {m.role === "user" ? (
              <div>
                <b>❓ {m.text}</b>
                <div style={{ marginTop: 4 }}>
                  {m.kbs && m.kbs.length ? m.kbs.map((id) => {
                    const kb = (kbs || []).find((k) => k.id === id);
                    return <Tag color="cyan" key={id}>{kb?.name || id.slice(0, 8)}</Tag>;
                  }) : <Tag>全部库</Tag>}
                </div>
              </div>
            ) : (
              m.result && <AssistantView r={m.result} mi={i} onCite={(docId, anchor) => setCite({ docId, anchor })} />
            )}
          </Card>
        ))}
        {loading && (
          <Card size="small" style={{ background: "rgba(99,102,241,0.06)" }}>
            <Typography.Text type="secondary">检索 + 生成中…（双路召回 + RRF + LLM 合成）</Typography.Text>
          </Card>
        )}
      </div>

      <Space.Compact style={{ width: "100%" }}>
        <Input value={q} onChange={(e) => setQ(e.target.value)} onPressEnter={ask}
          placeholder="问点什么…（如：双路召回怎么工作）" disabled={loading} size="large" />
        <Button type="primary" icon={<SendOutlined />} onClick={ask} loading={loading} size="large">问答</Button>
      </Space.Compact>

      {/* 右侧抽屉：问答设置（参考 RAGFlow，点开拉开、关闭缩回）*/}
      <Drawer title="问答设置（每次提问生效）" placement="right" open={cfgOpen} onClose={() => setCfgOpen(false)} width={360}>
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Field label="知识库（不选=全部）">
            <Select mode="multiple" allowClear maxTagCount="responsive" style={{ width: "100%" }}
              placeholder="全部可见库" value={s.kbIds} onChange={(v) => set({ kbIds: v })}
              options={(kbs || []).map((k) => ({ value: k.id, label: `${k.name}（${k.docCount}）` }))} />
          </Field>
          <Field label="LLM 模型">
            <Select style={{ width: "100%" }} allowClear placeholder="默认（租户 LLM）"
              value={s.modelId} onChange={(v) => set({ modelId: v })}
              options={(chatModels || []).map((m) => ({ value: m.id, label: `${m.name}${m.isDefault ? " · 默认" : ""}` }))} />
          </Field>
          <Field label={`温度（${s.temperature ?? "自动"}）`}>
            <Slider min={0} max={1} step={0.1} value={s.temperature ?? 0.7} onChange={(v) => set({ temperature: v })} />
          </Field>
          <Field label="最大 token">
            <InputNumber style={{ width: "100%" }} placeholder="模型默认" value={s.maxTokens} onChange={(v) => set({ maxTokens: v ?? undefined })} />
          </Field>
          <Field label="topK">
            <InputNumber style={{ width: "100%" }} min={1} value={s.topK} onChange={(v) => set({ topK: v ?? 8 })} />
          </Field>
          <Field label="相似度阈值（融合 score，留空=不过滤）">
            <InputNumber style={{ width: "100%" }} placeholder="不过滤" value={s.similarityThreshold} onChange={(v) => set({ similarityThreshold: v ?? undefined })} />
          </Field>
          <Field label="重排">
            <Select style={{ width: "100%" }} value={s.rerank === undefined ? "auto" : s.rerank ? "on" : "off"}
              onChange={(v) => set({ rerank: v === "auto" ? undefined : v === "on" })}
              options={[{ value: "auto", label: "自动（配了 rerank 模型才走）" }, { value: "on", label: "开" }, { value: "off", label: "关" }]} />
          </Field>
          <Field label="检索模式">
            <Select style={{ width: "100%" }} value={s.mode} onChange={(v) => set({ mode: v })}
              options={[{ value: "hybrid", label: "hybrid 双路" }, { value: "summary", label: "summary 路A" }, { value: "embedding", label: "embedding 路B" }]} />
          </Field>
          <Field label="引用（答案带 [n] + 来源）">
            <Switch checked={s.cite} onChange={(v) => set({ cite: v })} />
          </Field>
          <Field label="System Prompt（覆盖默认 RAG 提示）">
            <Input.TextArea rows={3} placeholder="留空用默认（仅据资料回答 + [n] 标注来源）" value={s.systemPrompt}
              onChange={(e) => set({ systemPrompt: e.target.value || undefined })} />
          </Field>
          <Button block onClick={() => setS({ ...DEFAULTS })}>恢复默认</Button>
        </Space>
      </Drawer>

      <CitationDrawer docId={cite?.docId ?? null} anchor={cite?.anchor ?? null} open={!!cite} onClose={() => setCite(null)} />
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 12, marginBottom: 4, color: "#8aa0c0" }}>{label}</div>
      {children}
    </div>
  );
}

function AssistantView({ r, mi, onCite }: { r: ChatResult; mi: number; onCite: (docId: string, anchor: string) => void }) {
  const rs = r.route_stats;
  return (
    <>
      {r.error && <Alert type="warning" showIcon message={r.error} style={{ marginBottom: 8 }} />}
      {r.answer ? (
        <div className="kb-md">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
            a: ({ href, children }) => <a href={href} style={{ color: "#22d3ee" }}>{children}</a>,
            code: ({ className, children }) => (
              <code className={className} style={{
                background: "rgba(148,163,184,0.16)", padding: "1px 5px", borderRadius: 4, fontSize: "0.9em",
              }}>{children}</code>
            ),
          }}>{linkifyCitations(r.answer, mi)}</ReactMarkdown>
        </div>
      ) : (
        <Typography.Text type="secondary">（未生成答案）</Typography.Text>
      )}

      {r.references.length > 0 && (
        <>
          <Divider style={{ margin: "10px 0" }} />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>来源</Typography.Text>
          <ol style={{ margin: "4px 0 0", paddingLeft: 20 }}>
            {r.references.map((ref) => (
              <li key={ref.index} id={`ref-${mi}-${ref.index}`} style={{ fontSize: 13, marginBottom: 2 }}>
                <a onClick={() => onCite(ref.docId, ref.chunkId)} style={{ color: "#67e8f9", cursor: "pointer" }}>
                  {ref.docId.slice(0, 8)}… · p.{ref.page ?? "?"}
                </a>
                <Typography.Text type="secondary" style={{ marginLeft: 6 }}>{ref.snippet.slice(0, 80)}…</Typography.Text>
              </li>
            ))}
          </ol>
        </>
      )}

      <div style={{ marginTop: 8 }}>
        <Space size={4} wrap>
          <Tag color="blue">路A {rs.path_a}</Tag>
          <Tag color="purple">路B {rs.path_b}</Tag>
          <Tag>{rs.degraded}</Tag>
          <Tag color={rs.rerank_used ? "gold" : undefined}>{rs.rerank_used ? "rerank ✓" : "RRF"}</Tag>
          <Tag>{rs.latency_ms} ms</Tag>
          {r.model && <Tag color="geekblue">{r.model}</Tag>}
        </Space>
      </div>
    </>
  );
}
