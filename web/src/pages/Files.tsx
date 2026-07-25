import { useState } from "react";
import { Table, Upload, Button, Space, Tag, Typography, Modal, Select, Input, Popconfirm, message } from "antd";
import { InboxOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listFiles, uploadToDrive, deleteFile, renameFile, attachFile } from "../api";
import type { DriveFile, ParserConfig } from "../types";
import type { UploadProps } from "antd";
import { listKbs } from "../api";
import ParserConfigFields from "../components/ParserConfigFields";

const fmtSize = (b: number | null) =>
  b == null ? "-" : b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1048576).toFixed(1)} MB`;

export default function Files() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["files"], queryFn: listFiles });
  const { data: kbs } = useQuery({ queryKey: ["kbs"], queryFn: listKbs });
  const [attachTarget, setAttachTarget] = useState<DriveFile | null>(null);
  const [kbId, setKbId] = useState<string | undefined>();
  const [pcfg, setPcfg] = useState<ParserConfig>({ method: "naive" });
  const [renameTarget, setRenameTarget] = useState<DriveFile | null>(null);
  const [newName, setNewName] = useState("");

  const upload = useMutation({
    mutationFn: (f: File) => uploadToDrive(f),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["files"] });
      message.success(d.reused ? "已存在，复用" : "已上传到个人库（未解析）");
    },
  });
  const props: UploadProps = { multiple: false, showUploadList: false, customRequest: ({ file }) => upload.mutate(file as File) };

  const delMut = useMutation({
    mutationFn: deleteFile,
    onSuccess: () => { message.success("已删除"); qc.invalidateQueries({ queryKey: ["files"] }); },
  });
  const renameMut = useMutation({
    mutationFn: (args: { id: string; name: string }) => renameFile(args.id, { name: args.name }),
    onSuccess: () => { message.success("已重命名"); setRenameTarget(null); qc.invalidateQueries({ queryKey: ["files"] }); },
  });
  const attachMut = useMutation({
    mutationFn: () => attachFile(attachTarget!.fileId, kbId!, pcfg),
    onSuccess: (d) => {
      message.success(d.status === "ready" ? "已挂载并解析" : "已挂载");
      setAttachTarget(null);
      qc.invalidateQueries({ queryKey: ["files"] });
    },
  });

  return (
    <>
      <Typography.Title level={3}>个人文件库</Typography.Title>
      <Typography.Text type="secondary">上传到个人空间（不立即解析），再选知识库挂载触发解析。文件可挂多个库。</Typography.Text>
      <Upload.Dragger {...props} style={{ marginTop: 12, marginBottom: 16 }}>
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p>点击或拖拽上传到个人库（PDF / DOCX / PPTX / XLSX / HTML / MD）</p>
      </Upload.Dragger>
      <Button icon={<ReloadOutlined />} onClick={() => qc.invalidateQueries({ queryKey: ["files"] })} style={{ marginBottom: 8 }}>刷新</Button>
      <Table<DriveFile>
        rowKey="fileId"
        loading={isLoading}
        dataSource={data}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "状态", dataIndex: "status", width: 110, render: (s) => <Tag color={s === "ready" ? "green" : s === "uploaded" ? "blue" : "orange"}>{s}</Tag> },
          { title: "分块", dataIndex: "parserType", width: 90, render: (t) => <Tag>{t || "naive"}</Tag> },
          { title: "已挂库", dataIndex: "kbCount", width: 80 },
          { title: "大小", dataIndex: "sizeBytes", width: 100, render: fmtSize },
          {
            title: "操作", width: 240,
            render: (_: unknown, r: DriveFile) => (
              <Space>
                <Button size="small" type="primary" onClick={() => { setAttachTarget(r); setKbId(undefined); setPcfg({ method: r.parserType || "naive" }); }}>加入知识库</Button>
                <Button size="small" onClick={() => { setRenameTarget(r); setNewName(r.name); }}>重命名</Button>
                <Popconfirm title="硬删该文件（从所有库 + 索引）？" onConfirm={() => delMut.mutate(r.fileId)}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      {/* 加入知识库 */}
      <Modal
        title={`加入知识库 · ${attachTarget?.name || ""}`}
        open={!!attachTarget}
        onCancel={() => setAttachTarget(null)}
        onOk={() => attachMut.mutate()}
        confirmLoading={attachMut.isPending}
        okButtonProps={{ disabled: !kbId }}
      >
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, marginBottom: 4 }}>选择知识库</div>
          <Select
            style={{ width: "100%" }}
            value={kbId}
            onChange={setKbId}
            placeholder="选择要挂载的知识库"
            options={(kbs || []).map((k) => ({ value: k.id, label: k.name }))}
          />
        </div>
        <div style={{ fontSize: 12, marginBottom: 4 }}>分块配置（仅未解析时生效；已解析仅挂载，不重切）</div>
        <ParserConfigFields value={pcfg} onChange={setPcfg} />
      </Modal>

      {/* 重命名 */}
      <Modal
        title="重命名"
        open={!!renameTarget}
        onCancel={() => setRenameTarget(null)}
        onOk={() => renameMut.mutate({ id: renameTarget!.fileId, name: newName })}
        confirmLoading={renameMut.isPending}
      >
        <Input value={newName} onChange={(e) => setNewName(e.target.value)} />
      </Modal>
    </>
  );
}
