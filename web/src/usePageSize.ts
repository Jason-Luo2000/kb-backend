import { useEffect, useState } from "react";

/** 表格每页行数（localStorage 记忆，跨知识库/文件库/成员页一致）。默认 20。 */
export function usePageSize(key = "kb_page_size") {
  const [pageSize, setPageSize] = useState<number>(() => {
    const v = Number(localStorage.getItem(key));
    return v > 0 ? v : 20;
  });
  useEffect(() => {
    localStorage.setItem(key, String(pageSize));
  }, [pageSize, key]);
  return [pageSize, setPageSize] as const;
}

/** AntD Table 的 pagination 配置（含每页行数选择器 + 总数）。 */
export function paginationProps(pageSize: number, setPageSize: (n: number) => void) {
  return {
    pageSize,
    showSizeChanger: true,
    pageSizeOptions: [10, 20, 50, 100],
    onShowSizeChange: (_: number, size: number) => setPageSize(size),
    showTotal: (total: number) => `共 ${total} 条`,
  };
}
