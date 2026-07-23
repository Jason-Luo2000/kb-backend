"""端到端验证：建库 → 上传样例 → 双路检索 → 引用。
前置：docker compose up --build + .env 已填 ZHIPU_API_KEY。运行：python scripts/e2e_demo.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk"))
from kb_sdk import KBClient  # noqa: E402

BASE = os.getenv("KB_BACKEND_URL", "http://localhost:8000")
KEY = os.getenv("KB_API_KEY", "kb_dev_api_key")

SAMPLE = os.path.join(os.path.dirname(__file__), "sample.md")
_BASE = """## {title}
{body}
"""
_SECTIONS = [
    ("双路召回", "本系统采用「总结文档导航」与「向量召回」并行的双路检索。路 A 先检索总结文档，再通过锚点回到原文同一位置精读真实原文，避免摘要失真直接进入上下文；路 B 走 embedding 与 BM25 混合召回，取 top-N 切块。两路结果用 RRF 融合后交模型生成答案。路 A 的价值在于宏观与跨章综合问题，路 B 擅长局部语义命中，二者互补。"),
    ("总结文档", "上传文件后，模型对全文生成结构化总结，每条总结携带 source_chunk_ids 锚点指回原文位置，作为路 A 的检索层。总结生成时强制 chunk_id 白名单约束防止模型编造锚点，覆盖率不足时会降权或跳过路 A。"),
    ("锚点稳定性", "锚点使用章节号与内容指纹的稳定锚，而非脆弱的字符偏移。文档重新切分后可通过指纹重定位，避免锚点静默失效导致路 A 读取错位原文。这是路 A 安全阀的核心。"),
    ("退化与兜底", "当总结遗漏、锚点漂移、或小文件场景时，路 A 会自动降级，仅依赖路 B 的向量召回兜底，确保不会因路 A 失效而给出错误答案。系统持续监控路 A 完成率与两路冲突率。"),
    ("权限与多租户", "企业版支持多知识库与用户授权矩阵，单文件可归入多个知识库而不复制存储。权限模型采用 RBAC 与 ABAC 结合，检索时强制注入租户与密级过滤，逐块回查防止越权。"),
    ("摄取管线", "文档上传后经解析、分块、嵌入、总结、锚点生成五阶段异步管线处理。分块采用 naive 策略按 token 数与重叠率切分，每块沉淀页码与章节路径。嵌入与总结可切换模型，MVP 用智谱，生产可换 BGE-M3。"),
]
MD = "# 企业级知识库系统\n\n" + "".join(_BASE.format(title=t, body=b) for t, b in _SECTIONS)


def main():
    c = KBClient(BASE, KEY, os.getenv("KB_USER_ID", "u_demo"))
    print("health:", c.health())

    kb = c.create_kb(f"demo-kb-{int(time.time())}", "e2e 验证用")
    kb_id = kb["id"]
    print("created kb:", kb_id)

    if not os.path.exists(SAMPLE):
        open(SAMPLE, "w").write(MD)

    print("uploading + ingesting (含解析/分块/embedding/总结)...")
    t = time.time()
    res = c.upload(kb_id, SAMPLE)
    print(f"  ingested in {time.time() - t:.1f}s:", res.get("stats"))

    queries = [
        "双路召回是什么，两路分别做什么",
        "总结文档如何定位回原文位置",
        "锚点在文档更新后如何保持有效",
    ]
    for q in queries:
        r = c.search(q, [kb_id])
        st = r.get("route_stats", {})
        print(f"\nQ: {q}")
        print(f"  route: A={st.get('path_a')} B={st.get('path_b')} degraded={st.get('degraded')} {st.get('latency_ms')}ms")
        for h in r.get("hits", [])[:3]:
            print(f"  [{h['path']}] page={h['page']} score={h['score']} :: {h['snippet'][:100].strip()}")

    # 引用示例（取首个命中的 chunk）
    first = c.search(queries[0], [kb_id]).get("hits", [])
    if first:
        cit = c.cite("双路召回是总结导航+向量并行。", [first[0]["chunkId"]])
        print("\ncite references:", cit.get("references"))

    print("\n✓ e2e done. pi 接入：ln -s ~/Developer/kb-backend/pi-ext ~/.pi/agent/extensions/kb")


if __name__ == "__main__":
    main()
