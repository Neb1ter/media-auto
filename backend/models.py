"""
数据库模型定义
"""
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, Float, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "db", "media_ops.db")

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class Article(Base):
    """文章草稿"""
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    content = Column(Text)
    summary = Column(Text)
    tags = Column(String(500))
    category = Column(String(100))
    status = Column(String(50), default="draft")  # draft, published, scheduled
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    ai_generated = Column(Boolean, default=False)
    word_count = Column(Integer, default=0)


class PublishTask(Base):
    """发布任务"""
    __tablename__ = "publish_tasks"

    id = Column(Integer, primary_key=True, index=True)
    article_id = Column(Integer)
    platform = Column(String(100), nullable=False)
    account_name = Column(String(200))
    status = Column(String(50), default="pending")  # pending, running, success, failed
    scheduled_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    error_msg = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    result_url = Column(String(500))


class PlatformAccount(Base):
    """平台账号"""
    __tablename__ = "platform_accounts"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(100), nullable=False)
    account_name = Column(String(200))
    cookie_file = Column(String(500))
    status = Column(String(50), default="active")  # active, expired, disabled
    last_check = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    notes = Column(Text)


class AIConfig(Base):
    """AI 模型配置"""
    __tablename__ = "ai_configs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    provider = Column(String(100))  # openai, baidu, alibaba, tencent
    api_key = Column(String(500))
    api_base = Column(String(500))
    model_name = Column(String(200))
    is_default = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)


class HotTopic(Base):
    """热点话题"""
    __tablename__ = "hot_topics"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500))
    source = Column(String(100))
    heat_score = Column(Float, default=0)
    url = Column(String(500))
    fetched_at = Column(DateTime, default=datetime.now)
    used = Column(Boolean, default=False)


class SystemLog(Base):
    """系统日志"""
    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, index=True)
    level = Column(String(50))
    module = Column(String(100))
    message = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


def init_db():
    """初始化数据库"""
    Base.metadata.create_all(bind=engine)
    print("✅ 数据库初始化完成")
