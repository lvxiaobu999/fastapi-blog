from .base import Base

# 包入口只导出无副作用的 ORM Base。
# Engine 和 Session 需要读取环境配置，使用方应显式从 app.db.session 导入，
# 避免仅导入模型时就初始化数据库连接配置。
__all__ = ["Base"]
