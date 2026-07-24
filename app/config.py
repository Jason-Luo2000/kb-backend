"""集中配置：从环境变量 / .env 读取。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 智谱（GLM + Embedding），复用 Claude Code / pi 同一把 key
    zhipu_api_key: str = ""
    zhipu_llm_model: str = "glm-5.2"
    zhipu_llm_base_url: str = "https://open.bigmodel.cn/api/anthropic"
    zhipu_embed_model: str = "embedding-3"
    zhipu_embed_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    zhipu_embed_dim: int = 2048

    # 基础设施
    database_url: str = "postgresql+psycopg://kb:kb_dev_pwd@localhost:5432/kb"
    es_url: str = "http://localhost:9200"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "kb"
    minio_secret_key: str = "kb_dev_pwd"
    minio_bucket: str = "kb-objects"
    redis_url: str = "redis://localhost:6379/0"

    # 服务
    kb_api_key: str = "kb_dev_api_key"
    kb_backend_url: str = "http://localhost:8000"
    kb_user_id: str = "u_demo"  # default 用户的 external_id；bootstrap 据此种 owner + api_key

    # 多租户（T9）：bootstrap 种 default 租户/owner/api_key，使 MVP 客户端无需改动即落入 default 租户
    default_tenant_name: str = "default"

    # 摄取 / 检索参数
    chunk_token_num: int = 512
    chunk_overlap: float = 0.1
    min_tokens_to_summarize: int = 1500
    default_top_k: int = 8
    store_mode: str = "es"  # es | memory（无容器环境用 memory 验证业务逻辑，生产用 es）
    path_a_theta: float = 0.2  # 路 A gross-miss 软门控（生产真 embedding 用 0.2，验证哈希伪向量设 -1）
    path_a_timeout_ms: int = 1000
    path_b_timeout_ms: int = 600
    # T10 锚点重定位 simhash Hamming 阈值（校准：重切分最优匹配中位 4/最大 7，不相关 ~30）
    path_a_relocate_hamming: int = 8  # relocated 判据（边界漂移仍命中）；valid 用紧阈值 3（块未变）
    # T11 outbox relay
    outbox_max_attempts: int = 3


settings = Settings()

# psycopg 用的是 libpq URL（不带 +psycopg）
PG_DSN = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
