"""Elasticsearch 客户端 + 索引/mapping（约定式：BM25 全文 + KNN 向量）。

中文 BM25：MVP 用 ES 默认 analyzer（按字切，能跑）；生产装 IK 分词器 + 自研 IDF（见方案 §4.1）。
"""
from elasticsearch import Elasticsearch

from app.config import settings

INDEX = "kb_chunks"

_client: Elasticsearch | None = None


def get_es():
    global _client
    if _client is None:
        if settings.store_mode == "memory":
            from app.es_memory import FakeES

            _client = FakeES()
        else:
            _client = Elasticsearch(settings.es_url)
    return _client


def ensure_index() -> None:
    es = get_es()
    if es.indices.exists(index=INDEX):
        return
    es.indices.create(
        index=INDEX,
        mappings={
            "properties": {
                "content_tks": {"type": "text"},
                "q_vec_vec": {
                    "type": "dense_vector",
                    "dims": settings.zhipu_embed_dim,
                    "index": True,
                    "similarity": "cosine",
                },
                "file_id_kwd": {"type": "keyword"},
                "tenant_id_kwd": {"type": "keyword"},  # T9：租户隔离纵深过滤
                "sensitivity_int": {"type": "integer"},  # T9：clearance ABAC（sensitivity<=clearance）
                "doc_type_kwd": {"type": "keyword"},  # chunk | summary | toc
                "is_summary_int": {"type": "integer"},
                "source_chunk_ids_kwd": {"type": "keyword"},
                "page_num_int": {"type": "integer"},
                "chunk_order_int": {"type": "integer"},
                "chunk_version_int": {"type": "integer"},
                "available_int": {"type": "integer"},
            }
        },
    )
