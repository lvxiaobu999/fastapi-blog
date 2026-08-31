"""Jinja2 模板环境；集中提供模板目录与只读功能开关。"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core import get_settings

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_DIR / "templates")

# QQ 登录按钮只在 Client ID、Client Secret 和回调地址同时配置后显示。这里不把任何
# 密钥传入模板；模板只能读取布尔开关。修改环境变量后需重启应用重新加载 Settings。
_settings = get_settings()
templates.env.globals["qq_login_enabled"] = bool(
    _settings.qq_client_id and _settings.qq_client_secret and _settings.qq_redirect_uri
)
