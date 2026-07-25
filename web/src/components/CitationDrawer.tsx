import { Drawer, Spin, Typography, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { readAnchor } from "../api";

interface Props {
  docId: string | null;
  anchor: string | null;
  open: boolean;
  onClose: () => void;
}

/** 点引用 → 调 read_anchor 回原文窗口精读 */
export default function CitationDrawer({ docId, anchor, open, onClose }: Props) {
  const enabled = open && !!docId && !!anchor;
  const { data, isLoading, error } = useQuery({
    queryKey: ["anchor", docId, anchor],
    queryFn: () => readAnchor(docId!, anchor!),
    enabled,
  });

  return (
    <Drawer title="原文窗口" open={open} onClose={onClose} width={640}>
      {isLoading && <Spin />}
      {error && <Typography.Text type="danger">锚点读取失败（可能已失效）</Typography.Text>}
      {data && (
        <>
          <div style={{ marginBottom: 12 }}>
            <Tag color="blue">第 {data.page} 页</Tag>
            <Tag>doc {data.docId?.slice(0, 8)}</Tag>
            <Tag>anchor {String(data.anchor).slice(0, 8)}</Tag>
          </div>
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
            {data.text}
          </Typography.Paragraph>
        </>
      )}
    </Drawer>
  );
}
