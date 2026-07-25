import { useState } from "react";
import { Input, Button, Select, List, Typography, Tag, Space, Card } from "antd";
import { SendOutlined } from "@ant-design/icons";
import { search } from "../api";
import type { Hit, SearchResult } from "../types";
import CitationDrawer from "../components/CitationDrawer";

interface Msg {
  role: "user" | "assistant";
  text?: string;
  result?: SearchResult;
}

export default function Chat() {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [topK, setTopK] = useState(8);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [loading, setLoading] = useState(false);
  const [cite, setCite] = useState<{ docId: string; anchor: string } | null>(null);

  async function ask() {
    if (!q.trim() || loading) return;
    const question = q;
    setQ("");
    setMsgs((m) => [...m, { role: "user", text: question }]);
    setLoading(true);
    try {
      const r = await search(question, undefined, topK, mode); // knowledgeBaseIds 空 → 全部可见库
      setMsgs((m) => [...m, { role: "assistant", result: r }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <Typography.Title level={3}>问答（检索全部可见知识库）</Typography.Title>
      <div style={{ maxHeight: "58vh", overflowY: "auto", marginBottom: 16, paddingRight: 8 }}>
        {msgs.map((m, i) => (
          <Card
            key={i}
            size="small"
            style={{ marginBottom: 8, background: m.role === "user" ? "#e6f4ff" : "#fff" }}
          >
            {m.role === "user" ? (
              <b>❓ {m.text}</b>
            ) : (
              m.result && (
                <>
                  <Space size={4} wrap style={{ marginBottom: 8 }}>
                    <Tag color="blue">路A {m.result.route_stats.path_a}</Tag>
                    <Tag color="purple">路B {m.result.route_stats.path_b}</Tag>
                    <Tag>{m.result.route_stats.degraded}</Tag>
                    <Tag>{m.result.route_stats.latency_ms} ms</Tag>
                    {m.result.route_stats.path_a_completed_rate != null && (
                      <Tag>pa {m.result.route_stats.path_a_completed_rate}</Tag>
                    )}
                  </Space>
                  <List
                    size="small"
                    dataSource={m.result.hits}
                    locale={{ emptyText: "无命中（试试换词或上传文档）" }}
                    renderItem={(h: Hit) => (
                      <List.Item actions={[<a onClick={() => setCite({ docId: h.docId, anchor: h.chunkId })}>查看原文</a>]}>
                        <List.Item.Meta
                          title={
                            <span>
                              {h.path} · p.{h.page} · <Typography.Text type="secondary">score {h.score}</Typography.Text>
                            </span>
                          }
                          description={h.snippet}
                        />
                      </List.Item>
                    )}
                  />
                </>
              )
            )}
          </Card>
        ))}
        {loading && (
          <Card size="small">
            <Typography.Text type="secondary">检索中…（双路召回 + RRF 融合）</Typography.Text>
          </Card>
        )}
      </div>
      <Space.Compact style={{ width: "100%" }}>
        <Select
          value={mode}
          onChange={setMode}
          style={{ width: 130 }}
          options={[
            { value: "hybrid", label: "hybrid 双路" },
            { value: "summary", label: "summary 路A" },
            { value: "embedding", label: "embedding 路B" },
          ]}
        />
        <Select
          value={topK}
          onChange={setTopK}
          style={{ width: 90 }}
          options={[3, 5, 8, 12].map((n) => ({ value: n, label: `top${n}` }))}
        />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onPressEnter={ask}
          placeholder="问点什么…（如：双路召回怎么工作）"
          disabled={loading}
        />
        <Button type="primary" icon={<SendOutlined />} onClick={ask} loading={loading}>
          检索
        </Button>
      </Space.Compact>
      <CitationDrawer
        docId={cite?.docId ?? null}
        anchor={cite?.anchor ?? null}
        open={!!cite}
        onClose={() => setCite(null)}
      />
    </>
  );
}
