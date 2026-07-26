import { useState } from "react";
import { Card, Col, Row, Select, Table, Tag, Typography, Space } from "antd";
import { useQuery } from "@tanstack/react-query";
import { analyticsOverview, analyticsTopQueries, analyticsUsers, analyticsModels } from "../api";

const WINDOWS = [
  { value: 7, label: "近 7 天" },
  { value: 30, label: "近 30 天" },
  { value: 0, label: "全部" },
];

function Stat({ title, value, hint }: { title: string; value: React.ReactNode; hint?: string }) {
  return (
    <Card>
      <Typography.Text type="secondary">{title}</Typography.Text>
      <div className="kb-stat" style={{ marginTop: 6 }}>{value}</div>
      {hint && <Typography.Text type="secondary" style={{ fontSize: 11 }}>{hint}</Typography.Text>}
    </Card>
  );
}

export default function Analytics() {
  const [days, setDays] = useState(7);
  const { data: ov } = useQuery({ queryKey: ["an-overview", days], queryFn: () => analyticsOverview(days) });
  const { data: top } = useQuery({ queryKey: ["an-top", days], queryFn: () => analyticsTopQueries(days, 15) });
  const { data: users } = useQuery({ queryKey: ["an-users", days], queryFn: () => analyticsUsers(days) });
  const { data: models } = useQuery({ queryKey: ["an-models", days], queryFn: () => analyticsModels(days) });
  const maxCount = Math.max(...(top || []).map((t) => t.count), 1);

  return (
    <>
      <Space style={{ justifyContent: "space-between", width: "100%", marginBottom: 12 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>数据看板</Typography.Title>
        <Select style={{ width: 130 }} value={days} onChange={setDays} options={WINDOWS} />
      </Space>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Stat title="总问答（检索+问答）" value={ov?.total_qa ?? "-"} /></Col>
        <Col span={6}><Stat title="RAG 问答" value={ov?.chats ?? "-"} hint={`成功率 ${ov?.success_rate != null ? (ov.success_rate * 100).toFixed(0) + "%" : "-"}`} /></Col>
        <Col span={6}><Stat title="成功回答" value={ov?.answered ?? "-"} /></Col>
        <Col span={6}><Stat title="未找到答案" value={ov?.no_result ?? "-"} hint={`失败 ${ov?.error ?? 0}`} /></Col>
      </Row>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Stat title="活跃用户" value={ov?.active_users ?? "-"} /></Col>
        <Col span={6}><Stat title="摄入次数" value={ov?.uploads ?? "-"} /></Col>
        <Col span={6}><Stat title="rerank 用量" value={ov?.rerank_uses ?? "-"} /></Col>
        <Col span={6} />
      </Row>

      <Row gutter={16}>
        <Col span={12}>
          <Card title="高频问题" size="small" style={{ marginBottom: 16 }}>
            {(top || []).length === 0 ? (
              <Typography.Text type="secondary">暂无数据</Typography.Text>
            ) : (
              top!.map((t, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                  <div style={{ width: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 13 }} title={t.query}>{t.query}</div>
                  <div style={{ flex: 1, background: "rgba(148,163,184,0.10)", borderRadius: 4, height: 16 }}>
                    <div style={{ width: `${(t.count / maxCount) * 100}%`, height: "100%", background: "linear-gradient(90deg,#22d3ee,#6366f1)", borderRadius: 4 }} />
                  </div>
                  <div style={{ width: 36, textAlign: "right", fontSize: 13 }}>{t.count}</div>
                </div>
              ))
            )}
          </Card>
        </Col>
        <Col span={12}>
          <Card title="模型调用次数" size="small">
            <Table
              size="small" rowKey={(r) => r.model + r.type} pagination={false}
              dataSource={models || []}
              columns={[
                { title: "模型", dataIndex: "model" },
                { title: "类型", dataIndex: "type", width: 100, render: (t: string) => <Tag color={t === "llm" ? "geekblue" : "purple"}>{t === "llm" ? "LLM" : "嵌入"}</Tag> },
                { title: "调用次数", dataIndex: "calls", width: 100 },
              ]}
            />
          </Card>
        </Col>
      </Row>

      <Card title="用户用量" size="small" style={{ marginTop: 16 }}>
        <Table
          size="small" rowKey="userId" pagination={{ pageSize: 10 }}
          dataSource={users || []}
          columns={[
            { title: "用户", dataIndex: "externalId" },
            { title: "提问数", dataIndex: "queries", width: 100 },
            { title: "RAG 问答", dataIndex: "chats", width: 110 },
            { title: "上传文档", dataIndex: "uploads", width: 110 },
          ]}
        />
      </Card>
    </>
  );
}
