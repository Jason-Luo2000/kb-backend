import { useParams } from "react-router-dom";
import { Table, Upload, message, Tag, Typography, Button } from "antd";
import { InboxOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listDocs, uploadDoc } from "../api";
import type { Doc } from "../types";
import type { UploadProps } from "antd";

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
  const upload = useMutation({
    mutationFn: (f: File) => uploadDoc(kbId!, f),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["docs", kbId] });
      message.success(d.reused ? "已存在，复用（未重摄）" : `上传成功：${d.stats?.chunks ?? "?"} chunks`);
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
      <Button icon={<ReloadOutlined />} onClick={() => qc.invalidateQueries({ queryKey: ["docs", kbId] })} style={{ marginBottom: 8 }}>
        刷新
      </Button>
      <Table<Doc>
        rowKey="docId"
        loading={isLoading}
        dataSource={data}
        columns={[
          { title: "标题", dataIndex: "title" },
          { title: "状态", dataIndex: "status", width: 110, render: (s) => <Tag color={s === "ready" ? "green" : "orange"}>{s}</Tag> },
          { title: "页数", dataIndex: "pages", width: 80 },
          { title: "大小", dataIndex: "sizeBytes", width: 110, render: fmtSize },
        ]}
      />
    </>
  );
}
