import { useQuery } from "@tanstack/react-query";
import { Select, InputNumber, Input } from "antd";
import type { ParserConfig } from "../types";
import { getParserMethods } from "../api";

interface Props {
  value?: ParserConfig;
  onChange?: (v: ParserConfig) => void;
  /** 隐藏 layout_recognize（非 PDF 场景或简化用） */
  hideLayout?: boolean;
  /** 含「继承知识库默认」选项（method="" 表示继承）；上传面板用 */
  allowInherit?: boolean;
}

/** 分块配置字段组（受控）。KB Form 内经 <Form.Item name="parserConfig"> 注入 value/onChange；
 *  Docs 上传面板用 useState 直接传。method 选项来自后端 /v1/parser/methods。 */
export default function ParserConfigFields({ value = {}, onChange, hideLayout, allowInherit }: Props) {
  const { data: methods } = useQuery({ queryKey: ["parserMethods"], queryFn: getParserMethods });
  const set = (patch: Partial<ParserConfig>) => onChange?.({ ...value, ...patch });
  const inherited = !value.method;

  return (
    <>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
        <div>
          <div style={{ fontSize: 12, marginBottom: 4 }}>分块方法</div>
          <Select
            style={{ width: 220 }}
            value={value.method || (allowInherit ? "" : "naive")}
            onChange={(v) => set({ method: v })}
            showSearch
            optionFilterProp="label"
            options={[
              ...(allowInherit ? [{ value: "", label: "继承知识库默认" }] : []),
              ...(methods || []).map((m) => ({ value: m.name, label: m.label })),
            ]}
          />
        </div>
        {!inherited && (
          <>
            <div>
              <div style={{ fontSize: 12, marginBottom: 4 }}>块大小(token)</div>
              <InputNumber
                min={64}
                style={{ width: 120 }}
                value={value.chunk_token_num}
                placeholder="默认 512"
                onChange={(v) => set({ chunk_token_num: v ?? undefined })}
              />
            </div>
            <div>
              <div style={{ fontSize: 12, marginBottom: 4 }}>重叠(0-1)</div>
              <InputNumber
                min={0}
                max={1}
                step={0.05}
                style={{ width: 100 }}
                value={value.overlap}
                placeholder="默认 0.1"
                onChange={(v) => set({ overlap: v ?? undefined })}
              />
            </div>
            <div>
              <div style={{ fontSize: 12, marginBottom: 4 }}>分隔符(delimiter 方法用)</div>
              <Input
                style={{ width: 160 }}
                value={value.delimiter}
                placeholder="如 。！？；"
                onChange={(e) => set({ delimiter: e.target.value })}
              />
            </div>
            {!hideLayout && (
              <div>
                <div style={{ fontSize: 12, marginBottom: 4 }}>PDF 版式识别</div>
                <Select
                  style={{ width: 150 }}
                  value={value.layout_recognize || "plaintext"}
                  onChange={(v) => set({ layout_recognize: v })}
                  options={[
                    { value: "plaintext", label: "plaintext（默认）" },
                    { value: "deepdoc", label: "DeepDOC（需容器）" },
                    { value: "mineru", label: "MinerU（需容器）" },
                    { value: "paddleocr", label: "PaddleOCR（需容器）" },
                  ]}
                />
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
