import { useState } from "react";
import { useParams } from "react-router-dom";
import { Table, Upload, message, Tag, Typography, Button, Collapse, Space, Popconfirm, Modal, Input } from "antd";
import { UploadOutlined, ReloadOutlined, SettingOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listDocs, uploadDoc, removeDocFromKb, reparseDoc, renameDoc, bulkDocs } from "../api";
import type { Doc, ParserConfig } from "../types";
import type { UploadProps } from "antd";
import ParserConfigFields from "../components/ParserConfigFields";

const fmtSize = (b: number | null) =>
  b == null ? "-" : b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1048576).toFixed(1)} MB`;

export default function Docs() {
  const { kbId } = useParams();
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["docs", kbId],
    queryFn: () => listDocs(kbId!),
    enabled: !!kbId,
    refetchInterval: (query) => (query.state.data?.some((x: Doc) => x.status !== "ready") ? 3000 : false),
  });
  // 上传默认「继承知识库默认」(method="")；选了具体方法才随上传下发
  const [parseCfg, setParseCfg] = useState<ParserConfig>({ method: "" });
  const [selected, setSelected] = useState<string[]>([]);
  const [renameTarget, setRenameTarget] = useState<Doc | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const [cfgTarget, setCfgTarget] = useState<Doc | null>(null);
  const [cfg, setCfg] = useState<ParserConfig>({ method: "naive" });

  const upload = useMutation({
    mutationFn: (f: File) => uploadDoc(kbId!, f, parseCfg.method ? parseCfg : undefined),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["docs", kbId] });
      message.success(d.reused ? "已存在，复用（未重摄）" : `上传成功：${d.stats?.chunks ?? "?"} chunks · ${d.stats?.mode ?? ""}`);
    },
  });
  const props: UploadProps = { multiple: false, showUploadList: false, customRequest: ({ file }) => upload.mutate(file as File) };

  const removeMut = useMutation({
    mutationFn: (docId: string) => removeDocFromKb(kbId!, docId),
    onSuccess: () => { message.success("已移出该库"); qc.invalidateQueries({ queryKey: ["docs", kbId] }); },
  });
  // 改方法/参数并重新解析（每文档独立方法）
  const reparseWithCfg = useMutation({
    mutationFn: () => reparseDoc(kbId!, cfgTarget!.docId, cfg.method ? cfg : undefined),
    onSuccess: (d) => {
      message.success(`已应用并重解析 v${d.stats.version}（${cfg.method || "继承"}）`);
      setCfgTarget(null);
      qc.invalidateQueries({ queryKey: ["docs", kbId] });
    },
  });
  const renameMut = useMutation({
    mutationFn: () => renameDoc(kbId!, renameTarget!.docId, newTitle),
    onSuccess: () => { message.success("已重命名"); setRenameTarget(null); qc.invalidateQueries({ queryKey: ["docs", kbId] }); },
  });
  const bulkMut = useMutation({
    mutationFn: (action: "delete" | "reparse") => bulkDocs(kbId!, selected, action),
    onSuccess: (d, action) => {
      message.success(`${action === "delete" ? "移出" : "重解析"} ${d.done.length} 个`);
      setSelected([]);
      qc.invalidateQueries({ queryKey: ["docs", kbId] });
    },
  });

  const openCfg = (r: Doc) => {
    setCfgTarget(r);
    // 预填：该文档现有 parser_config；无则用其 parserType
    setCfg(r.parserConfig ? { ...r.parserConfig } : { method: r.parserType || "naive" });
  };

  return (
    <>
      <Typography.Title level={3}>文档（库 {kbId?.slice(0, 8)}…）</Typography.Title>
      <Typography.Text type="secondary">每个文档可独立设分块方法（点「配置」改方法并重解析）。新文档默认继承知识库配置。</Typography.Text>
      <Space style={{ marginTop: 12, marginBottom: 12 }}>
        <Upload {...props}>
          <Button type="primary" icon={<UploadOutlined />}>上传文档</Button>
        </Upload>
        <Button icon={<ReloadOutlined />} onClick={() => qc.invalidateQueries({ queryKey: ["docs", kbId] })}>刷新</Button>
        {selected.length > 0 && (
          <>
            <Popconfirm title={`移出 ${selected.length} 个文档？`} onConfirm={() => bulkMut.mutate("delete")}>
              <Button danger>批量移出 ({selected.length})</Button>
            </Popconfirm>
            <Button loading={bulkMut.isPending} onClick={() => bulkMut.mutate("reparse")}>批量重解析 ({selected.length})</Button>
          </>
        )}
      </Space>

      <Collapse
        style={{ marginBottom: 12 }}
        items={[{
          key: "cfg",
          label: <Space><SettingOutlined /> 本次上传分块设置（默认继承知识库）</Space>,
          children: <ParserConfigFields value={parseCfg} onChange={setParseCfg} allowInherit />,
        }]}
      />

      <Table<Doc>
        rowKey="docId"
        loading={isLoading}
        dataSource={data}
        rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys.map(String)) }}
        columns={[
          { title: "标题", dataIndex: "title" },
          { title: "分块方法", dataIndex: "parserType", width: 110, render: (t) => <Tag color="blue">{t || "naive"}</Tag> },
          { title: "状态", dataIndex: "status", width: 100, render: (s) => <Tag color={s === "ready" ? "green" : "orange"}>{s}</Tag> },
          { title: "页数", dataIndex: "pages", width: 70 },
          { title: "大小", dataIndex: "sizeBytes", width: 100, render: fmtSize },
          {
            title: "操作", width: 230,
            render: (_: unknown, r: Doc) => (
              <Space>
                <Button size="small" type="primary" onClick={() => openCfg(r)}>配置/重解析</Button>
                <Button size="small" onClick={() => { setRenameTarget(r); setNewTitle(r.title); }}>重命名</Button>
                <Popconfirm title="移出该库（文件保留在个人库）？" onConfirm={() => removeMut.mutate(r.docId)}>
                  <Button size="small" danger>移出</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      {/* 配置 + 重解析（每文档独立方法）*/}
      <Modal
        title={`分块配置 · ${cfgTarget?.title || ""}`}
        open={!!cfgTarget}
        onCancel={() => setCfgTarget(null)}
        onOk={() => reparseWithCfg.mutate()}
        confirmLoading={reparseWithCfg.isPending}
        okText="应用并重新解析"
      >
        <ParserConfigFields value={cfg} onChange={setCfg} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          改方法/参数后点「应用并重新解析」生效（生成新版本；T12 增量自动复用未变部分）。
        </Typography.Text>
      </Modal>

      <Modal
        title="重命名"
        open={!!renameTarget}
        onCancel={() => setRenameTarget(null)}
        onOk={() => renameMut.mutate()}
        confirmLoading={renameMut.isPending}
      >
        <Input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} />
      </Modal>
    </>
  );
}
