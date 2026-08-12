"""应用健康检查、Host 白名单和基础安全响应头测试。"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

pytestmark = pytest.mark.anyio


async def test_liveness_and_readiness(client: AsyncClient) -> None:
    """存活检查不访问依赖，就绪检查确认测试数据库和 Redis 都可用。"""

    live = await client.get("/health/live")
    ready = await client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert live.headers["content-security-policy"].startswith("default-src 'self'")
    assert live.headers["x-content-type-options"] == "nosniff"
    assert live.headers["x-frame-options"] == "DENY"


async def test_untrusted_host_is_rejected() -> None:
    """Host Header 不在白名单时，应用在进入业务路由前拒绝请求。"""

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://attacker.example") as client:
        response = await client.get("/health/live")

    assert response.status_code == 400
