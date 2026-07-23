"""AuthzEngine（Python，Cedar 形状）：RBAC(租户角色) + kb_grant + clearance>=sensitivity(ABAC)。

resolve(principal) -> AuthzDecision{allowed_kb_ids, clearance, kb_roles}。
业务代码只依赖 resolve() / AuthzDecision / can_write / is_kb_admin 接口，
后期可整体替换为 cedar-py 实现而不改调用方。

授权规则（T9）：
- tenant owner/admin → 该租户全部 kb（admin 监督模型）；
- editor → visibility∈{team,tenant} 的 kb（读）；
- viewer → 仅显式 grant 的 kb；
- 任意角色 + 有效 kb_grant → 该 kb（role 取 grant 与可见 role 的较高者）；
- clearance = max(tenant role clearance, 各 grant role clearance)；ABAC 求 clearance>=sensitivity（T9 sensitivity 全 0）。
跨租户 grant 不生效：list_kbs / _allowed_file_ids 均再按 tenant_id 收敛。
"""
from dataclasses import dataclass, field

from app.db import get_conn
from app.middleware.auth import Principal

CLEARANCE = {"viewer": 1, "editor": 2, "admin": 3, "owner": 4}
SENSITIVITY = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 4}


@dataclass
class AuthzDecision:
    allowed_kb_ids: list[str] = field(default_factory=list)
    clearance: int = 0
    kb_roles: dict[str, str] = field(default_factory=dict)  # kb_id -> role


def _higher(r1: str, r2: str) -> str:
    return r1 if CLEARANCE.get(r1, 0) >= CLEARANCE.get(r2, 0) else r2


def resolve(principal: Principal) -> AuthzDecision:
    allowed: set[str] = set()
    kb_roles: dict[str, str] = {}
    clearance = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT role FROM kb_user_tenant WHERE user_id=%s AND tenant_id=%s",
                (principal.user_id, principal.tenant_id),
            )
            row = cur.fetchone()
            tenant_role = row[0] if row else "viewer"

            # 显式授权（未撤销、未过期）
            cur.execute(
                """SELECT kb_id, role FROM kb_grant
                   WHERE user_id=%s AND revoked_at IS NULL
                     AND (expires_at IS NULL OR expires_at > now())""",
                (principal.user_id,),
            )
            grants = {str(r[0]): r[1] for r in cur.fetchall()}

            if tenant_role in ("owner", "admin"):
                cur.execute("SELECT id FROM kb_kb WHERE tenant_id=%s", (principal.tenant_id,))
                for (kid,) in cur.fetchall():
                    allowed.add(str(kid))
                    kb_roles[str(kid)] = _higher(kb_roles.get(str(kid), "viewer"), tenant_role)
                clearance = max(clearance, CLEARANCE[tenant_role])
            elif tenant_role == "editor":
                cur.execute(
                    "SELECT id FROM kb_kb WHERE tenant_id=%s AND visibility IN ('team','tenant')",
                    (principal.tenant_id,),
                )
                for (kid,) in cur.fetchall():
                    allowed.add(str(kid))
                    kb_roles.setdefault(str(kid), "viewer")
                clearance = max(clearance, CLEARANCE["editor"])

            for kid, role in grants.items():
                allowed.add(kid)
                kb_roles[kid] = _higher(kb_roles.get(kid, "viewer"), role)
                clearance = max(clearance, CLEARANCE.get(role, 0))

    return AuthzDecision(
        allowed_kb_ids=sorted(allowed),
        clearance=clearance,
        kb_roles=kb_roles,
    )


def can_write(decision: AuthzDecision, kb_id: str) -> bool:
    """editor+ 可写（上传/删除）。"""
    return CLEARANCE.get(decision.kb_roles.get(kb_id, ""), 0) >= CLEARANCE["editor"]


def is_kb_admin(decision: AuthzDecision, kb_id: str) -> bool:
    """admin+ 可授权（grant/revoke）。"""
    return CLEARANCE.get(decision.kb_roles.get(kb_id, ""), 0) >= CLEARANCE["admin"]
