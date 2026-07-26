import { useState } from "react";
import { Table, Upload, Button, Space, Tag, Typography, Modal, Select, Input, Popconfirm, message } from "antd";
import { UploadOutlined, ReloadOutlined, LinkOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listFiles, uploadToDrive, deleteFile, renameFile, attachFile, listKbs } from "../api";
import type { DriveFile, ParserConfig } from "../types";
import type { UploadProps } from "antd";
import ParserConfigFields from "../components/ParserConfigFields";

const fmtSize = (b: number | null) =>
  b == null ? "-" : b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1048576).toFixed(1)} MB`;

export default function Files() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["files"], queryFn: listFiles });
  const { data: kbs } = useQuery({ queryKey: ["kbs"], queryFn: listKbs });
  const [selected, setSelected] = useState<string[]>([]);
  const [attachTargets, setAttachTargets] = useState<DriveFile[] | null>(null);
  const [kbIds, setKbIds] = useState<string[]>([]);
  const [pcfg, setPcfg] = useState<ParserConfig>({ method: "" });
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
  // 文件 × 知识库（均可多）：逐对 attach，汇总成功/失败。首个未解析文件触发 ingest，其余仅链接
  const attachMut = useMutation({
    mutationFn: async () => {
      const targets = attachTargets ?? [];
      const cfg = pcfg.method ? pcfg : undefined;
      let ok = 0, fail = 0, total = 0;
      for (const t of targets) {
        for (const kb of kbIds) {
          total++;
          try { await attachFile(t.fileId, kb, cfg); ok++; } catch { fail++; }  // noqa: await-in-loop
        }
      }
      return { ok, fail, total };
    },
    onSuccess: (r) => {
      message.success(`已挂载 ${r.ok}/${r.total}${r.fail ? `，失败 ${r.fail}` : ""}`);
      setAttachTargets(null);
      setSelected([]);
      setKbIds([]);
      qc.invalidateQueries({ queryKey: ["files"] });
    },
  });

  const openAttach = (targets: DriveFile[]) => {
    setAttachTargets(targets);
    setKbIds([]);
    // 单个→预填该文件现有方法；多个→默认继承知识库
    setPcfg(targets.length === 1 ? { method: targets[0].parserType || "naive" } : { method: "" });
  };
  const selectedFiles = (data || []).filter((f) => selected.includes(f.fileId));
  const opCount = (attachTargets?.length ?? 0) * kbIds.length;

  return (
    <>
      <Typography.Title level={3}>个人文件库</Typography.Title>
      <Typography.Text type="secondary">上传到个人空间（不立即解析），再选知识库挂载触发解析。支持多文件、多知识库。</Typography.Text>
      <Space style={{ marginTop: 12, marginBottom: 12 }}>
        <Upload {...props}>
          <Button type="primary" icon={<UploadOutlined />}>上传文件</Button>
        </Upload>
        <Button icon={<ReloadOutlined />} onClick={() => qc.invalidateQueries({ queryKey: ["files"] })}>刷新</Button>
        {selected.length > 0 && (
          <Button type="primary" icon={<LinkOutlined />} onClick={() => openAttach(selectedFiles)}>
            加入知识库（{selected.length}）
          </Button>
        )}
      </Space>
      <Table<DriveFile>
        rowKey="fileId"
        loading={isLoading}
        dataSource={data}
        rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys.map(String)) }}
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
                <Button size="small" type="primary" onClick={() => openAttach([r])}>加入知识库</Button>
                <Button size="small" onClick={() => { setRenameTarget(r); setNewName(r.name); }}>重命名</Button>
                <Popconfirm title="硬删该文件（从所有库 + 索引）？" onConfirm={() => delMut.mutate(r.fileId)}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      {/* 加入知识库（单/多文件 × 单/多知识库）*/}
      <Modal
        title={
          (attachTargets?.length ?? 0) > 1
            ? `批量加入知识库（${attachTargets?.length} 个文件）`
            : `加入知识库 · ${attachTargets?.[0]?.name || ""}`
        }
        open={!!attachTargets}
        onCancel={() => setAttachTargets(null)}
        onOk={() => attachMut.mutate()}
        confirmLoading={attachMut.isPending}
        okButtonProps={{ disabled: opCount === 0 }}
        okText={opCount ? `挂载 ${opCount} 项` : "挂载"}
      >
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, marginBottom: 4 }}>选择知识库（可多选，文件会同时挂到所选每个库）</div>
          <Select
            mode="multiple"
            style={{ width: "100%" }}
            value={kbIds}
            onChange={setKbIds}
            placeholder="选择一个或多个知识库"
            options={(kbs || []).map((k) => ({ value: k.id, label: k.name }))}
          />
        </div>
        <div style={{ fontSize: 12, marginBottom: 4 }}>分块配置（仅未解析文件生效；已解析仅挂载）</div>
        <ParserConfigFields value={pcfg} onChange={setPcfg} allowInherit />
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
