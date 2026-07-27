#!/usr/bin/env bash
# kb-backend 一键干净部署：从模板生成随机凭证 → docker compose 起全栈 → 打印登录信息。
# 设计：模板零真实 key；所有密钥现场生成并写进 .env（必须跨重启/迁移保留）。
set -euo pipefail

cd "$(dirname "$0")"

GEN="__GENERATE__"
gen() { openssl rand -hex 24; }    # 48 位十六进制

# --- 1. 初始化 .env（仅从模板；绝不读现有真实 .env）---
if [ ! -f .env ]; then
    echo "→ 从 .env.example 创建 .env"
    cp .env.example .env
fi
# 幂等：补齐任何仍是占位符的密钥（无论 .env 是刚建还是已存在）
for var in POSTGRES_PASSWORD MINIO_ACCESS_KEY MINIO_SECRET_KEY KB_API_KEY MODEL_SECRET; do
    if grep -q "^${var}=${GEN}$" .env; then
        val="$(gen)"
        case "$(uname)" in
            Darwin) sed -i '' "s|^${var}=${GEN}$|${var}=${val}|" .env ;;
            *)      sed -i    "s|^${var}=${GEN}$|${var}=${val}|" .env ;;
        esac
        echo "  · 生成 ${var}"
    fi
done

# --- 2. Linux 下 ES 需要 vm.max_map_count（提示，不自动 sudo）---
if [ "$(uname -s)" = "Linux" ]; then
    cur="$(cat /proc/sys/vm/max_map_count 2>/dev/null || echo 0)"
    if [ "${cur:-0}" -lt 262144 ]; then
        echo "⚠️  Elasticsearch 需要：sudo sysctl -w vm.max_map_count=262144"
        echo "   设好后重新运行本脚本（macOS Docker Desktop 一般已满足）。"
    fi
fi

# --- 3. 起栈 ---
echo "→ docker compose up --build -d"
docker compose up --build -d

# --- 4. 等后端就绪（直连 :8000，不依赖 nginx）---
echo "→ 等待 backend 就绪..."
ready=0
for _ in $(seq 1 60); do
    if curl -fs http://localhost:8000/healthz >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 2
done

api_key="$(grep '^KB_API_KEY=' .env | head -1 | cut -d= -f2-)"
echo ""
if [ "$ready" = "1" ]; then
    echo "✓ 服务就绪"
else
    echo "⚠️  60s 内 /healthz 未就绪——用 'docker compose logs kb-backend' 排障"
fi
cat <<EOF

=========================================================
 kb-backend 已启动
---------------------------------------------------------
 控制台（浏览器）:  http://localhost
 API 直连（SDK/pi）: http://localhost:8000
 登录 key (owner):  ${api_key}
---------------------------------------------------------
 下一步：用上面的 key 登录 → 「模型管理」添加任意渠道的
         embedding + LLM（OpenAI / Anthropic / Gemini /
         本地 / DeepSeek 等）→ 之后即可上传文档、问答。
---------------------------------------------------------
 ⚠️ 妥善保存 .env：含 MODEL_SECRET，丢失 = 库里模型 key 作废。
    重启：docker compose down && ./deploy.sh（保留 .env）。
=========================================================
EOF
