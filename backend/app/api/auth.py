"""访问控制接口：口令登录 / 登出 / 会话检查（决策 D12）。

口令来自环境变量 ACCESS_PASSWORD；未设置时保护整体关闭：
此时 /check 恒返回 ok=True（前端不弹登录页），/login 恒拒绝（登录页本就不会出现）。
"""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from .. import security

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginBody, response: Response) -> dict:
    if not security.check_password(body.password):
        return {"ok": False, "error": "口令不正确"}
    response.set_cookie(
        security.COOKIE_NAME,
        security.make_token(),
        max_age=security.TOKEN_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(security.COOKIE_NAME)
    return {"ok": True}


@router.get("/check")
def check(request: Request) -> dict:
    if not security.protection_enabled():
        return {"ok": True}
    return {"ok": security.verify_token(request.cookies.get(security.COOKIE_NAME))}
