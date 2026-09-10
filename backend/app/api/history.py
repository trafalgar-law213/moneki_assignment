"""问答历史 API：跨会话长期记忆（pgvector 语义检索）。

会话内记忆（追问"那五月呢"）由前端回传历史实现；本模块补的是**跨会话**：
每次问答自动入库（见 api/chat.py 的 _save_history），此后可语义检索——
关掉浏览器、换设备、服务重启，历史仍在。

设计取舍：历史答案**不直接喂给 AI 的数字链路**（数字必须来自本次查询，
见 README「数据库演进」后的语义检索说明）；本 API 服务前端展示与用户自查。
"""

from fastapi import APIRouter, HTTPException

from .. import db as dbmod
from .. import query
from ..ai import embeddings

router = APIRouter(prefix="/api")

MAX_LIMIT = 20


@router.get("/history/recent")
def history_recent(limit: int = 20):
    conn = dbmod.get_conn()
    try:
        return query.recent_history(conn, min(limit, MAX_LIMIT))
    finally:
        conn.close()


@router.get("/history/search")
def history_search(q: str, limit: int = 5):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q 不能为空")
    try:
        emb = embeddings.embed([q])[0]
    except RuntimeError as exc:  # 模型文件缺失 → 明确 503，前端可提示
        raise HTTPException(status_code=503, detail=str(exc))
    conn = dbmod.get_conn()
    try:
        return query.search_history(conn, emb, min(limit, MAX_LIMIT))
    finally:
        conn.close()
