/**
 * pi 扩展：把 kb-backend 包装成工具 + 注入「知识库助手」人设 + /kb 命令。
 * 安装：ln -s ~/Developer/kb-backend/pi-ext ~/.pi/agent/extensions/kb
 * 环境变量：KB_BACKEND_URL / KB_USER_TOKEN(=后端 KB_API_KEY) / KB_USER_ID
 *
 * T17：HTTP 调用改用可复用 kb_client（幂等重试 + 分级超时 + 结构化错误）。
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { makeKbClient } from "./kb_client";

const BASE = process.env.KB_BACKEND_URL ?? "http://localhost:8000";
const TOKEN = process.env.KB_USER_TOKEN ?? process.env.KB_SERVICE_TOKEN ?? "";
const ASUSER = process.env.KB_USER_ID ?? "";

const kb = makeKbClient({ baseUrl: BASE, token: TOKEN, asUser: ASUSER });

type Kb = { id: string; name: string; description?: string; docCount: number };
let kbs: Kb[] = [];
const refreshKbs = async () => {
	try {
		kbs = await kb.listKbs<Kb[]>();
	} catch {
		/* keep last */
	}
};

export default function kbExtension(pi: ExtensionAPI): void {
	pi.registerTool({
		name: "kb_search",
		label: "知识库检索",
		description: "双路召回(总结导航+向量)并 RRF 融合。回答文档问题前必先调用。",
		promptSnippet: "kb_search(query) — 检索带原文锚点的材料",
		promptGuidelines: [
			"回答事实/资料类问题前，必先 kb_search 检索知识库。",
			"对高分命中用 kb_read_anchor 回原文精读锚点再综合。",
			"生成答案后必须调 kb_cite 回传以获取精确引用；禁止自行编造来源标注。",
		],
		parameters: Type.Object({
			query: Type.String({ description: "搜索关键词或问题" }),
			knowledgeBaseIds: Type.Optional(Type.Array(Type.String())),
			topK: Type.Optional(Type.Number()),
			mode: Type.Optional(Type.Union([Type.Literal("hybrid"), Type.Literal("summary"), Type.Literal("embedding")])),
		}),
		executionMode: "parallel",
		async execute(_id, p) {
			const h = await kb.search(p.query, p.knowledgeBaseIds, p.topK, p.mode ?? "hybrid");
			return { content: [{ type: "text", text: JSON.stringify(h, null, 2) }] };
		},
	});

	pi.registerTool({
		name: "kb_read_anchor",
		label: "精读原文位置",
		description: "按锚点(=chunkId)回原文窗口精读真实原文",
		promptSnippet: "kb_read_anchor(docId,anchor) — 回原文精读锚点处",
		parameters: Type.Object({
			docId: Type.String(),
			anchor: Type.String(),
			before: Type.Optional(Type.Number()),
			after: Type.Optional(Type.Number()),
		}),
		executionMode: "parallel",
		async execute(_id, p) {
			const t = await kb.readAnchor(p.docId, p.anchor, p.before, p.after);
			return { content: [{ type: "text", text: typeof t === "string" ? t : JSON.stringify(t) }] };
		},
	});

	pi.registerTool({
		name: "kb_list",
		label: "列出知识库",
		description: "列出当前可用知识库",
		parameters: Type.Object({}),
		async execute(_id, _p) {
			kbs = await kb.listKbs<Kb[]>();
			return { content: [{ type: "text", text: JSON.stringify(kbs, null, 2) }] };
		},
	});

	pi.registerTool({
		name: "kb_cite",
		label: "回传引用",
		description: "答案生成后回传 answer+chunkIds，后端返回带引用标注的结果",
		promptSnippet: "答案后调 kb_cite(answer,chunkIds) 获取精确引用",
		parameters: Type.Object({ answer: Type.String(), chunkIds: Type.Array(Type.String()) }),
		async execute(_id, p) {
			const r = await kb.cite(p.answer, p.chunkIds);
			return { content: [{ type: "text", text: JSON.stringify(r) }] };
		},
	});

	// 注意：kb_admin（grant/upload/purge）不作为 LLM 工具（红队：防 prompt-injection 提权），仅 admin UI/SDK。
	pi.registerCommand({
		name: "kb",
		description: "选择本会话默认知识库 / 列库",
		async handler(_args, ctx: any) {
			if (ctx.mode !== "tui") {
				ctx.ui.notify("请在 TUI 中使用 /kb", "warning");
				return;
			}
			await refreshKbs();
			const items = kbs.map((k) => `${k.id} · ${k.name}`);
			if (!items.length) {
				ctx.ui.notify("暂无知识库", "info");
				return;
			}
			const sel = await ctx.ui.select("选择默认知识库", items);
			if (sel) {
				const kbId = sel.split(" · ")[0];
				(pi as any).appendEntry?.("kb-selection", { kbId });
				ctx.ui.notify(`已选: ${sel}`);
			}
		},
	});

	pi.on("session_start" as any, async () => {
		await refreshKbs();
	});

	pi.on("before_agent_start" as any, (e: any) => ({
		systemPrompt: (e.systemPrompt ?? "") +
			[
				"",
				"【角色】你是企业知识库助手。",
				"回答事实/资料类问题前必先 kb_search；对高分命中用 kb_read_anchor 回原文精读锚点再综合；",
				"生成答案后必须调 kb_cite 回传以获取精确引用，禁止自行编造来源；最终展示 kb_cite 返回的带标注答案。",
				`当前可见库：${kbs.map((k) => `${k.id}(${k.name})`).join(", ") || "（未加载，先 kb_list）"}`,
			].join("\n"),
	}));
}
