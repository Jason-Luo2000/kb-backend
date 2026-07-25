# kb-web · 前端控制台

kb-backend 的 Web 控制台（Vite + React + TypeScript + Ant Design），SPA 调后端 HTTP API。

## 功能

- **登录**：API-key（+ 后端地址，dev 留空走 proxy）
- **知识库**：列表 / 新建 / 点进文档
- **文档**：拖拽上传（PDF/DOCX/PPTX/XLSX/HTML/MD）/ 列表 / 状态轮询（parsing→ready）
- **问答**：聊天式双路检索 + RRF，每条命中带「查看原文」（read_anchor 原文窗口）
- **授权**（admin）：grant / revoke
- **运维**（owner）：配额、GC（dry_run/apply）、对账、审计哈希链验链、锚快照、outbox 修剪
- **监控**：/readyz 组件状态 + 关键指标（请求总数/摄入/检索 p95）+ 原始 /metrics 链接

> 门控：非 owner 隐藏运维/监控，非 admin 隐藏授权（据 `/v1/me` 的 is_owner/is_admin）。

## 开发

后端跑在 :8001（`STORE_MODE=memory PATH_A_THETA=-1 .venv/bin/uvicorn app.main:app --port 8001`），前端 Vite proxy 同源免 CORS：

```bash
cd web
npm install
npm run dev      # → http://localhost:5173
```

浏览器打开 :5173，用 `kb_dev_api_key` 登录。

## 构建

```bash
npm run build    # tsc --noEmit + vite build → dist/
npm run preview  # 本地预览构建产物
```

`dist/` 可由后端 `StaticFiles` 挂载（单端口）或 nginx 单独托管。

## 环境变量

- `VITE_API_BASE`（可选）：后端地址。dev 留空走 Vite proxy（:5173→:8001）；生产若前端与后端不同源，设为后端 URL。
- 后端需开 CORS：`CORS_ORIGINS=http://localhost:5173`（默认已含，逗号分隔多源）。

## 安全提示

API-key 存 localStorage（XSS 风险，内部工具可接受）——非可信网络勿用，后期可换 httpOnly cookie + SSO（T25）。
