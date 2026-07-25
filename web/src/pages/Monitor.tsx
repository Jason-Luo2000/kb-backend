import { useMemo } from "react";
import { Card, Row, Col, Tag, Typography, Button } from "antd";
import { useQuery } from "@tanstack/react-query";
import { readyz, fetchMetrics } from "../api";

/** 汇总某 metric 所有样本值（跨 label 组合）。跳过 # HELP / # TYPE 行。 */
function sumMetric(text: string, name: string): number | null {
  let sum = 0;
  let found = false;
  for (const line of text.split("\n")) {
    if (line.startsWith(name)) {
      const parts = line.split(/\s+/);
      const val = parseFloat(parts[parts.length - 1]);
      if (!Number.isNaN(val)) {
        sum += val;
        found = true;
      }
    }
  }
  return found ? sum : null;
}

function firstMetric(text: string, name: string): number | null {
  for (const line of text.split("\n")) {
    if (line.startsWith(name)) {
      const parts = line.split(/\s+/);
      const val = parseFloat(parts[parts.length - 1]);
      if (!Number.isNaN(val)) return val;
    }
  }
  return null;
}

function Stat({ title, value }: { title: string; value: React.ReactNode }) {
  return (
    <Card>
      <Typography.Text type="secondary">{title}</Typography.Text>
      <h2 style={{ margin: "4px 0 0" }}>{value}</h2>
    </Card>
  );
}

export default function Monitor() {
  const { data: rz } = useQuery({ queryKey: ["readyz"], queryFn: readyz, refetchInterval: 15000 });
  const { data: metrics } = useQuery({ queryKey: ["metrics"], queryFn: fetchMetrics, refetchInterval: 15000 });

  const stats = useMemo(
    () =>
      metrics
        ? {
            requests: sumMetric(metrics, "kb_http_requests_total"),
            ingest: sumMetric(metrics, "kb_ingest_count"),
            p95: firstMetric(metrics, "kb_retrieval_p95_ms"),
          }
        : null,
    [metrics]
  );

  return (
    <>
      <Typography.Title level={3}>监控</Typography.Title>
      <Typography.Text type="secondary">组件健康（/readyz，每 15s 刷新）</Typography.Text>
      <Row gutter={16} style={{ marginTop: 12, marginBottom: 24 }}>
        {(rz ? Object.entries(rz) : []).map(([k, v]) => (
          <Col key={k} span={6}>
            <Card>
              <Tag color={v === "ok" ? "green" : "red"} style={{ fontSize: 14 }}>
                {v}
              </Tag>
              <h3 style={{ margin: "8px 0 0" }}>{k}</h3>
            </Card>
          </Col>
        ))}
      </Row>
      <Row gutter={16}>
        <Col span={6}><Stat title="HTTP 请求总数" value={stats?.requests ?? "-"} /></Col>
        <Col span={6}><Stat title="摄入次数（近1h）" value={stats?.ingest ?? "-"} /></Col>
        <Col span={6}><Stat title="检索 p95 ms" value={stats?.p95 ?? "-"} /></Col>
      </Row>
      <Button href="/metrics" target="_blank" style={{ marginTop: 24 }}>
        原始 /metrics（Prometheus 文本）
      </Button>
    </>
  );
}
