import { useEffect, useRef, useState } from "react";
import { Input, Button, Select, Typography, Tag, Space, Drawer, Slider, InputNumber, Switch, Alert, Avatar, Dropdown, Modal, Empty } from "antd";
import { SendOutlined, SettingOutlined, RobotOutlined, UserOutlined, PlusOutlined, MoreOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  chat, listChatModels, listKbs, listConversations, createConversation, getConversation, renameConversation, deleteConversation,
} from "../api";
import type { ChatResult, Conversation, ConvMessage, RouteStats } from "../types";
import CitationDrawer from "../components/CitationDrawer";

interface Msg { role: "user" | "assistant"; text?: string; result?: ChatResult; kbs?: string[]; }
interface Settings {
  kbIds: string[]; modelId?: string; temperature?: number; maxTokens?: number;
  topK: number; similarityThreshold?: number; rerank?: boolean; mode: string; systemPrompt?: string; cite: boolean;
}
const DEFAULTS: Settings = { kbIds: [], topK: 8, mode: "hybrid", cite: true };
const linkify = (a: string, mi: number) => a.replace(/\[(\d+)\]/g, (_, n) => `[[${n}]](#ref-${mi}-${n})`);
const fromHistory = (ms: ConvMessage[]): Msg[] => ms.map((m) => m.role === "user"
  ? { role: "user", text: m.content || "" }
  : { role: "assistant", result: { answer: m.content, references: m.meta?.references || [], hits: [],
      route_stats: (m.meta?.route_stats || {}) as RouteStats, model: m.meta?.model ?? null, error: m.meta?.error ?? null } });

export default function Chat() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [s, setS] = useState<Settings>({ ...DEFAULTS });
  const [curId, setCurId] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [loading, setLoading] = useState(false);
  const [cfgOpen, setCfgOpen] = useState(false);
  const [cite, setCite] = useState<{ docId: string; anchor: string } | null>(null);
  const [renameTarget, setRenameTarget] = useState<{ id: string; title: string } | null>(null);
  const { data: kbs } = useQuery({ queryKey: ["kbs"], queryFn: listKbs });
  const { data: chatModels } = useQuery({ queryKey: ["chatModels"], queryFn: listChatModels });
  const { data: convs } = useQuery({ queryKey: ["conversations"], queryFn: listConversations });
  const set = (patch: Partial<Settings>) => setS((p) => ({ ...p, ...patch }));
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }, [msgs, loading]);
  // 首次加载：自动选最近会话
  useEffect(() => { if (convs && !curId && convs.length > 0) select(convs[0].id); /* eslint-disable-next-line */ }, [convs]);

  async function select(id: string) {
    setCurId(id);
    try { const c = await getConversation(id); setMsgs(fromHistory(c.messages)); } catch { /* */ }
  }
  async function newConv() {
    const c = await createConversation();
    setCurId(c.id); setMsgs([]); qc.invalidateQueries({ queryKey: ["conversations"] });
  }
  async function ask() {
    if (!q.trim() || loading) return;
    const question = q; setQ("");
    let convId = curId;
    if (!convId) { const c = await createConversation(); convId = c.id; setCurId(convId); }
    const history = msgs
      .map((m) => (m.role === "user" ? { role: "user" as const, content: m.text || "" } : { role: "assistant" as const, content: m.result?.answer || "" }))
      .filter((m) => m.content).slice(-8);
    setMsgs((m) => [...m, { role: "user", text: question, kbs: s.kbIds }]);
    setLoading(true);
    try {
      const r = await chat({
        query: question, knowledgeBaseIds: s.kbIds.length ? s.kbIds : undefined, modelId: s.modelId,
        systemPrompt: s.systemPrompt, temperature: s.temperature, maxTokens: s.maxTokens, topK: s.topK,
        similarityThreshold: s.similarityThreshold, rerank: s.rerank, mode: s.mode, cite: s.cite, history, conversationId: convId!,
      });
      setMsgs((m) => [...m, { role: "assistant", result: r }]);
      qc.invalidateQueries({ queryKey: ["conversations"] }); // 刷新侧栏（标题/预览/时间）
    } finally { setLoading(false); }
  }
  const delConv = useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onSuccess: (_d, id) => { if (curId === id) { setCurId(null); setMsgs([]); } qc.invalidateQueries({ queryKey: ["conversations"] }); },
  });
  const renMut = useMutation({
    mutationFn: () => renameConversation(renameTarget!.id, renameTarget!.title),
    onSuccess: () => { setRenameTarget(null); qc.invalidateQueries({ queryKey: ["conversations"] }); },
  });

  const modelName = (chatModels || []).find((m) => m.id === s.modelId)?.name;

  return (
    <div style={{ display: "flex", height: "calc(100vh - 132px)", gap: 12 }}>
      {/* 会话侧栏 */}
      <aside style={{ width: 230, flexShrink: 0, display: "flex", flexDirection: "column", borderRight: "1px solid rgba(148,163,184,0.16)" }}>
        <div style={{ padding: 8 }}><Button block icon={<PlusOutlined />} onClick={newConv}>新对话</Button></div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {(convs || []).length === 0 && <div style={{ padding: 16, color: "#6b7a93", fontSize: 13 }}>暂无会话</div>}
          {(convs || []).map((c) => (
            <div key={c.id} onClick={() => select(c.id)} style={{
              padding: "8px 10px", cursor: "pointer", borderBottom: "1px solid rgba(148,163,184,0.08)",
              background: c.id === curId ? "rgba(34,211,238,0.10)" : "transparent", display: "flex", justifyContent: "space-between", alignItems: "center",
            }}>
              <div style={{ overflow: "hidden", flex: 1 }}>
                <div style={{ fontSize: 13, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.title}</div>
                <div style={{ fontSize: 11, color: "#6b7a93", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {c.updatedAt?.slice(5, 16)} {c.preview ? "· " + c.preview.slice(0, 18) : ""}
                </div>
              </div>
              <Dropdown trigger={["click"]} menu={{ items: [
                { key: "r", label: "重命名", onClick: () => setRenameTarget({ id: c.id, title: c.title }) },
                { key: "d", label: "删除", danger: true, onClick: () => delConv.mutate(c.id) },
              ]}}>
                <MoreOutlined onClick={(e) => e.stopPropagation()} style={{ color: "#8aa0c0", padding: 4 }} />
              </Dropdown>
            </div>
          ))}
        </div>
      </aside>

      {/* 对话区 */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <Space style={{ justifyContent: "space-between", width: "100%", marginBottom: 6 }}>
          <Space size={6} wrap>
            <Tag color="cyan">库 {s.kbIds.length ? s.kbIds.length : "全部"}</Tag>
            <Tag color="geekblue">{modelName || "默认 LLM"}</Tag>
            <Tag>top{s.topK}</Tag>
            <Tag color={s.rerank === false ? undefined : "gold"}>{s.rerank === false ? "rerank 关" : s.rerank ? "rerank 开" : "rerank 自动"}</Tag>
          </Space>
          <Button icon={<SettingOutlined />} onClick={() => setCfgOpen(true)}>设置</Button>
        </Space>

        <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", paddingRight: 6 }}>
          {msgs.length === 0 && !loading && (
            <div style={{ textAlign: "center", color: "#6b7a93", marginTop: "16%" }}>
              <RobotOutlined style={{ fontSize: 40, marginBottom: 12 }} />
              <div>{curId ? "在这个会话里问点什么吧" : "点「新对话」开始，或选左侧历史会话"}</div>
            </div>
          )}
          {msgs.map((m, i) => m.role === "user" ? (
            <div className="kb-chat-row user" key={i}>
              <div className="kb-chat-bubble user">
                <div>{m.text}</div>
                {m.kbs && m.kbs.length > 0 && (
                  <div style={{ marginTop: 4 }}>{m.kbs.map((id) => { const kb = (kbs || []).find((k) => k.id === id); return <Tag key={id} style={{ marginRight: 4 }}>{kb?.name || id.slice(0, 8)}</Tag>; })}</div>
                )}
              </div>
              <Avatar size={32} className="kb-avatar-user" icon={<UserOutlined />} />
            </div>
          ) : (
            m.result && <AssistantTurn key={i} r={m.result} mi={i} onCite={(docId, anchor) => setCite({ docId, anchor })} />
          ))}
          {loading && (
            <div className="kb-chat-row" key="loading">
              <Avatar size={32} className="kb-avatar-bot" icon={<RobotOutlined />} />
              <div className="kb-chat-ans"><Typography.Text type="secondary">检索 + 生成中…</Typography.Text></div>
            </div>
          )}
        </div>

        <Space.Compact style={{ width: "100%", marginTop: 8 }} size="large">
          <Input value={q} onChange={(e) => setQ(e.target.value)} onPressEnter={ask} placeholder="问点什么…（Enter 发送）" disabled={loading} />
          <Button type="primary" icon={<SendOutlined />} onClick={ask} loading={loading}>发送</Button>
        </Space.Compact>
      </div>

      <Drawer title="问答设置" placement="right" open={cfgOpen} onClose={() => setCfgOpen(false)} width={360}>
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Field label="知识库（不选=全部）">
            <Select mode="multiple" allowClear maxTagCount="responsive" style={{ width: "100%" }} placeholder="全部可见库" value={s.kbIds} onChange={(v) => set({ kbIds: v })} options={(kbs || []).map((k) => ({ value: k.id, label: `${k.name}（${k.docCount}）` }))} />
          </Field>
          <Field label="LLM 模型"><Select style={{ width: "100%" }} allowClear placeholder="默认" value={s.modelId} onChange={(v) => set({ modelId: v })} options={(chatModels || []).map((m) => ({ value: m.id, label: `${m.name}${m.isDefault ? " · 默认" : ""}` }))} /></Field>
          <Field label={`温度（${s.temperature ?? "自动"}）`}><Slider min={0} max={1} step={0.1} value={s.temperature ?? 0.7} onChange={(v) => set({ temperature: v })} /></Field>
          <Field label="最大 token"><InputNumber style={{ width: "100%" }} placeholder="模型默认" value={s.maxTokens} onChange={(v) => set({ maxTokens: v ?? undefined })} /></Field>
          <Field label="topK"><InputNumber style={{ width: "100%" }} min={1} value={s.topK} onChange={(v) => set({ topK: v ?? 8 })} /></Field>
          <Field label="相似度阈值"><InputNumber style={{ width: "100%" }} placeholder="不过滤" value={s.similarityThreshold} onChange={(v) => set({ similarityThreshold: v ?? undefined })} /></Field>
          <Field label="重排"><Select style={{ width: "100%" }} value={s.rerank === undefined ? "auto" : s.rerank ? "on" : "off"} onChange={(v) => set({ rerank: v === "auto" ? undefined : v === "on" })} options={[{ value: "auto", label: "自动" }, { value: "on", label: "开" }, { value: "off", label: "关" }]} /></Field>
          <Field label="检索模式"><Select style={{ width: "100%" }} value={s.mode} onChange={(v) => set({ mode: v })} options={[{ value: "hybrid", label: "hybrid" }, { value: "summary", label: "summary" }, { value: "embedding", label: "embedding" }]} /></Field>
          <Field label="引用"><Switch checked={s.cite} onChange={(v) => set({ cite: v })} /></Field>
          <Field label="System Prompt"><Input.TextArea rows={3} placeholder="留空用默认" value={s.systemPrompt} onChange={(e) => set({ systemPrompt: e.target.value || undefined })} /></Field>
          <Button block onClick={() => setS({ ...DEFAULTS })}>恢复默认</Button>
        </Space>
      </Drawer>

      <Modal title="重命名会话" open={!!renameTarget} onCancel={() => setRenameTarget(null)} onOk={() => renMut.mutate()} confirmLoading={renMut.isPending}>
        <Input value={renameTarget?.title} onChange={(e) => setRenameTarget({ id: renameTarget!.id, title: e.target.value })} />
      </Modal>
      <CitationDrawer docId={cite?.docId ?? null} anchor={cite?.anchor ?? null} open={!!cite} onClose={() => setCite(null)} />
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (<div><div style={{ fontSize: 12, marginBottom: 4, color: "#8aa0c0" }}>{label}</div>{children}</div>);
}

function AssistantTurn({ r, mi, onCite }: { r: ChatResult; mi: number; onCite: (docId: string, anchor: string) => void }) {
  const rs = r.route_stats || ({} as RouteStats);
  return (
    <div className="kb-chat-row">
      <Avatar size={32} className="kb-avatar-bot" icon={<RobotOutlined />} />
      <div className="kb-chat-ans">
        {r.error && <Alert type="warning" showIcon message={r.error} style={{ marginBottom: 8 }} />}
        {r.answer ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
            a: ({ href, children }) => <a href={href} style={{ color: "#22d3ee" }}>{children}</a>,
            code: ({ className, children }) => <code className={className} style={{ background: "rgba(148,163,184,0.16)", padding: "1px 5px", borderRadius: 4, fontSize: "0.9em" }}>{children}</code>,
          }}>{linkify(r.answer, mi)}</ReactMarkdown>
        ) : <Typography.Text type="secondary">（未生成答案）</Typography.Text>}
        {(r.references || []).length > 0 && (
          <div style={{ marginTop: 4 }}>
            {r.references.map((ref) => (
              <a key={ref.index} id={`ref-${mi}-${ref.index}`} className="kb-ref-chip" onClick={() => onCite(ref.docId, ref.chunkId)}>
                [{ref.index}] {ref.docId.slice(0, 8)}… · p.{ref.page ?? "?"}
              </a>
            ))}
          </div>
        )}
        <div className="kb-chat-meta">
          <Tag>{rs.degraded}</Tag><Tag color={rs.rerank_used ? "gold" : undefined}>{rs.rerank_used ? "rerank" : "RRF"}</Tag>
          <Tag>{rs.latency_ms ?? "-"}ms</Tag>{r.model && <Tag color="geekblue">{r.model}</Tag>}
          <span>· 路A {rs.path_a ?? "-"} / 路B {rs.path_b ?? "-"}</span>
        </div>
      </div>
    </div>
  );
}
