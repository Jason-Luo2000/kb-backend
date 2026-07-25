import { useState } from "react";
import { useParams } from "react-router-dom";
import { Table, Upload, message, Tag, Typography, Button, Collapse, Space } from "antd";
import { InboxOutlined, ReloadOutlined, SettingOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listDocs, uploadDoc } from "../api";
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
  // 上传时分块配置（覆盖 KB 默认；留空字段→后端用 KB/默认）
  const [parseCfg, setParseCfg] = useState<ParserConfig>({ method: "naive" });

  const upload = useMutation({
    mutationFn: (f: File) => uploadDoc(kbId!, f, parseCfg),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["docs", kbId] });
      message.success(d.reused ? "已存在，复用（未重摄）" : `上传成功：${d.stats?.chunks ?? "?"} chunks · ${d.stats?.mode ?? ""}`);
    },
  });
  const props: UploadProps = {
    multiple: false,
    showUploadList: false,
    customRequest: ({ file }) => upload.mutate(file as File),
  };

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

      <Button icon={<ReloadOutlined />} onClick={() => qc.invalidateQueries({ queryKey: ["docs", kbId] })} style={{ marginBottom: 8 }}>
        刷新
      </Button>
      <Table<Doc>
        rowKey="docId"
        loading={isLoading}
        dataSource={data}
        columns={[
          { title: "标题", dataIndex: "title" },
          { title: "分块", dataIndex: "parserType", width: 100, render: (t) => <Tag>{t || "naive"}</Tag> },
          { title: "状态", dataIndex: "status", width: 110, render: (s) => <Tag color={s === "ready" ? "green" : "orange"}>{s}</Tag> },
          { title: "页数", dataIndex: "pages", width: 80 },
          { title: "大小", dataIndex: "sizeBytes", width: 110, render: fmtSize },
        ]}
      />
    </>
  );
}
