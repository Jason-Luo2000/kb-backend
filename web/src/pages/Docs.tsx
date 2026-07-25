import { useState } from "react";
import { useParams } from "react-router-dom";
import { Table, Upload, message, Tag, Typography, Button, Collapse, Space, Popconfirm, Modal, Input } from "antd";
import { InboxOutlined, ReloadOutlined, SettingOutlined } from "@ant-design/icons";
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
  const [parseCfg, setParseCfg] = useState<ParserConfig>({ method: "naive" });
  const [selected, setSelected] = useState<string[]>([]);
  const [renameTarget, setRenameTarget] = useState<Doc | null>(null);
  const [newTitle, setNewTitle] = useState("");

  const upload = useMutation({
    mutationFn: (f: File) => uploadDoc(kbId!, f, parseCfg),
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
  const reparseMut = useMutation({
    mutationFn: (docId: string) => reparseDoc(kbId!, docId),
    onSuccess: (d) => { message.success(`已重解析 v${d.stats.version}`); qc.invalidateQueries({ queryKey: ["docs", kbId] }); },
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

  return (
    <>
      <Typography.Title level={3}>文档（库 {kbId?.slice(0, 8)}…）</Typography.Title>
      <Upload.Dragger {...props} style={{ marginBottom: 16 }}>
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p>点击或拖拽上传（PDF / DOCX / PPTX / XLSX / HTML / MD）</p>
      </Upload.Dragger>

      <Collapse
        style={{ marginBottom: 16 }}
        items={[{
          key: "cfg",
          label: <Space><SettingOutlined /> 上传分块设置（覆盖该库默认）</Space>,
          children: <ParserConfigFields value={parseCfg} onChange={setParseCfg} />,
        }]}
      />

      <Space style={{ marginBottom: 8 }}>
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

      <Table<Doc>
        rowKey="docId"
        loading={isLoading}
        dataSource={data}
        rowSelection={{
          selectedRowKeys: selected,
          onChange: (keys) => setSelected(keys.map(String)),
        }}
        columns={[
          { title: "标题", dataIndex: "title" },
          { title: "分块", dataIndex: "parserType", width: 90, render: (t) => <Tag>{t || "naive"}</Tag> },
          { title: "状态", dataIndex: "status", width: 100, render: (s) => <Tag color={s === "ready" ? "green" : "orange"}>{s}</Tag> },
          { title: "页数", dataIndex: "pages", width: 70 },
          { title: "大小", dataIndex: "sizeBytes", width: 100, render: fmtSize },
          {
            title: "操作", width: 230,
            render: (_: unknown, r: Doc) => (
              <Space>
                <Button size="small" onClick={() => { setRenameTarget(r); setNewTitle(r.title); }}>重命名</Button>
                <Button size="small" loading={reparseMut.isPending} onClick={() => reparseMut.mutate(r.docId)}>重解析</Button>
                <Popconfirm title="移出该库（文件保留在个人库）？" onConfirm={() => removeMut.mutate(r.docId)}>
                  <Button size="small" danger>移出</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

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
