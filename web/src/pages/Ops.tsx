import { useState } from "react";
import { Card, Button, Descriptions, Space, message, Alert, Typography, Tag } from "antd";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getQuota, gc, reconcile, auditVerify, auditAnchor, pruneOutbox } from "../api";

const Pre = ({ data }: { data: unknown }) => (
  <pre style={{ marginTop: 12, background: "#fafafa", padding: 8, fontSize: 12, maxHeight: 240, overflow: "auto" }}>
    {JSON.stringify(data, null, 2)}
  </pre>
);

export default function Ops() {
  const qc = useQueryClient();
  const { data: quota } = useQuery({ queryKey: ["quota"], queryFn: getQuota });
  const { data: verify } = useQuery({ queryKey: ["auditVerify"], queryFn: auditVerify });
  const [gcReport, setGcReport] = useState<any>(null);
  const [rcReport, setRcReport] = useState<any>(null);

  const gcMut = useMutation({
    mutationFn: (dry: boolean) => gc(undefined, dry),
    onSuccess: (d) => {
      setGcReport(d);
      if (!d.dry_run) message.success("GC 已执行");
    },
  });
  const rcMut = useMutation({
    mutationFn: (dry: boolean) => reconcile(undefined, dry, true),
    onSuccess: (d) => {
      setRcReport(d);
      if (!d.dry_run) message.success("对账已修复");
    },
  });
  const anchorMut = useMutation({ mutationFn: auditAnchor, onSuccess: () => message.success("锚快照已写") });
  const pruneMut = useMutation({ mutationFn: () => pruneOutbox(), onSuccess: (d) => message.success(`修剪 ${d.deleted} 行`) });

  return (
    <>
      <Typography.Title level={3}>运维（owner）</Typography.Title>
      <Space direction="vertical" style={{ width: "100%" }}>
        <Card title="配额" size="small">
          {quota && (
            <Descriptions size="small" column={3}>
              <Descriptions.Item label="文档">
                {quota.usage.doc_count} / {quota.limits.max_docs || "∞"}
              </Descriptions.Item>
              <Descriptions.Item label="字节">
                {quota.usage.bytes} / {quota.limits.max_bytes || "∞"}
              </Descriptions.Item>
              <Descriptions.Item label="周期">{quota.usage.period}</Descriptions.Item>
            </Descriptions>
          )}
        </Card>

        <Card
          title="审计哈希链"
          size="small"
          extra={<Button onClick={() => qc.invalidateQueries({ queryKey: ["auditVerify"] })}>刷新</Button>}
        >
          {verify &&
            (verify.verified ? (
              <Alert type="success" message={`链完整（${verify.rows} 行，gap ${verify.gaps}）`} />
            ) : (
              <Alert
                type="error"
                message={`检测到篡改：字段 ${verify.recomputed_mismatches} / 链路 ${verify.prev_hash_breaks}`}
              />
            ))}
          <Space style={{ marginTop: 12 }}>
            <Button onClick={() => anchorMut.mutate()} loading={anchorMut.isPending}>
              写锚快照
            </Button>
            <Tag>外部发布（WORM/签名）待定</Tag>
          </Space>
        </Card>

        <Card title="GC（旧版本回收）" size="small">
          <Space>
            <Button onClick={() => gcMut.mutate(true)} loading={gcMut.isPending}>
              dry_run 报告
            </Button>
            <Button danger onClick={() => gcMut.mutate(false)} loading={gcMut.isPending}>
              执行 apply
            </Button>
          </Space>
          {gcReport && <Pre data={gcReport} />}
        </Card>

        <Card title="对账（ES↔PG 漂移）" size="small">
          <Space>
            <Button onClick={() => rcMut.mutate(true)} loading={rcMut.isPending}>
              dry_run 报告
            </Button>
            <Button danger onClick={() => rcMut.mutate(false)} loading={rcMut.isPending}>
              修复 apply
            </Button>
          </Space>
          {rcReport && <Pre data={rcReport} />}
        </Card>

        <Card title="outbox 修剪" size="small">
          <Button onClick={() => pruneMut.mutate()} loading={pruneMut.isPending}>
            修剪（默认 7 天）
          </Button>
        </Card>
      </Space>
    </>
  );
}
