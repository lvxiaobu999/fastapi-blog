"""SQLAlchemy ORM 模型共用的声明式基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的父类，用于集中保存表结构的 MetaData。"""

    pass
