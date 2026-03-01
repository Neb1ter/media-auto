"""
自媒体运营自动化平台 - FastAPI 后端
"""
import asyncio
import os
import sys
from datetime import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from loguru import logger

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from models import (
    init_db, get_db, Article, PublishTask, PlatformAccount,
    AIConfig, HotTopic, SessionLocal
)
from ai_creator import AICreator, PLATFORM_STYLES
from publisher import PublisherManager, PLATFORM_CONFIGS

# ===================== 初始化 =====================
app = FastAPI(
    title="自媒体运营自动化平台",
    description="AI 驱动的多平台内容创作与发布管理系统",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化数据库
init_db()

# ===================== Pydantic 模型 =====================

class GenerateTitlesRequest(BaseModel):
    topic: str
    platform: str = "general"
    count: int = 5

class GenerateArticleRequest(BaseModel):
    title: str
    platform: str = "general"
    outline: Optional[str] = None
    keywords: Optional[List[str]] = None

class RewriteRequest(BaseModel):
    content: str
    platform: str
    style_hint: Optional[str] = ""

class AdaptRequest(BaseModel):
    title: str
    content: str
    platforms: List[str]

class ArticleCreate(BaseModel):
    title: str
    content: str
    summary: Optional[str] = ""
    tags: Optional[str] = ""
    category: Optional[str] = ""
    ai_generated: bool = False

class PublishRequest(BaseModel):
    article_id: int
    platforms: List[str]
    scheduled_at: Optional[str] = None

class HotTopicsRequest(BaseModel):
    category: str = "科技"

class AIConfigCreate(BaseModel):
    name: str
    provider: str
    api_key: str
    api_base: Optional[str] = None
    model_name: str = "gpt-4.1-mini"
    is_default: bool = False


# ===================== 工具函数 =====================

def get_ai_creator(db: Session) -> AICreator:
    """获取 AI 创作器（使用默认配置或环境变量）"""
    config = db.query(AIConfig).filter(AIConfig.is_default == True, AIConfig.is_active == True).first()
    if config:
        return AICreator(api_key=config.api_key, api_base=config.api_base, model=config.model_name)
    # 使用环境变量中的默认配置
    return AICreator()


# ===================== API 路由 =====================

@app.get("/")
async def root():
    return {"message": "自媒体运营自动化平台 API", "version": "1.0.0", "status": "running"}

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ---- AI 内容创作 ----

@app.post("/api/ai/titles")
async def generate_titles(req: GenerateTitlesRequest, db: Session = Depends(get_db)):
    """生成文章标题"""
    try:
        creator = get_ai_creator(db)
        titles = creator.generate_titles(req.topic, req.platform, req.count)
        return {"success": True, "titles": titles, "count": len(titles)}
    except Exception as e:
        logger.error(f"生成标题失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ai/outline")
async def generate_outline(req: GenerateTitlesRequest, db: Session = Depends(get_db)):
    """生成文章大纲"""
    try:
        creator = get_ai_creator(db)
        outline = creator.generate_outline(req.topic, req.platform)
        return {"success": True, "outline": outline}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ai/article")
async def generate_article(req: GenerateArticleRequest, db: Session = Depends(get_db)):
    """生成完整文章"""
    try:
        creator = get_ai_creator(db)
        result = creator.generate_article(req.title, req.outline, req.platform, req.keywords)

        # 自动保存到草稿
        article = Article(
            title=result["title"],
            content=result["content"],
            summary=result.get("summary", ""),
            tags="",
            ai_generated=True,
            word_count=result.get("word_count", 0),
            status="draft"
        )
        db.add(article)
        db.commit()
        db.refresh(article)

        return {"success": True, "article_id": article.id, **result}
    except Exception as e:
        logger.error(f"生成文章失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ai/rewrite")
async def rewrite_article(req: RewriteRequest, db: Session = Depends(get_db)):
    """改写文章"""
    try:
        creator = get_ai_creator(db)
        rewritten = creator.rewrite_article(req.content, req.platform, req.style_hint or "")
        return {"success": True, "content": rewritten}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ai/adapt")
async def adapt_article(req: AdaptRequest, db: Session = Depends(get_db)):
    """一键适配多平台"""
    try:
        creator = get_ai_creator(db)
        results = creator.adapt_for_platform(req.title, req.content, req.platforms)
        return {"success": True, "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ai/hot-topics")
async def get_hot_topics(req: HotTopicsRequest, db: Session = Depends(get_db)):
    """获取热门话题"""
    try:
        creator = get_ai_creator(db)
        topics = creator.fetch_hot_topics(req.category)
        return {"success": True, "topics": topics}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ai/tags")
async def generate_tags(req: GenerateArticleRequest, db: Session = Depends(get_db)):
    """生成标签"""
    try:
        creator = get_ai_creator(db)
        tags = creator.generate_tags(req.title, req.outline or "", req.platform)
        return {"success": True, "tags": tags}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---- 文章管理 ----

@app.get("/api/articles")
async def list_articles(skip: int = 0, limit: int = 20, db: Session = Depends(get_db)):
    """获取文章列表"""
    articles = db.query(Article).order_by(Article.created_at.desc()).offset(skip).limit(limit).all()
    total = db.query(Article).count()
    return {
        "total": total,
        "articles": [
            {
                "id": a.id,
                "title": a.title,
                "summary": a.summary,
                "tags": a.tags,
                "category": a.category,
                "status": a.status,
                "word_count": a.word_count,
                "ai_generated": a.ai_generated,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            }
            for a in articles
        ]
    }

@app.get("/api/articles/{article_id}")
async def get_article(article_id: int, db: Session = Depends(get_db)):
    """获取文章详情"""
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    return {
        "id": article.id,
        "title": article.title,
        "content": article.content,
        "summary": article.summary,
        "tags": article.tags,
        "category": article.category,
        "status": article.status,
        "word_count": article.word_count,
        "ai_generated": article.ai_generated,
        "created_at": article.created_at.isoformat() if article.created_at else None,
    }

@app.post("/api/articles")
async def create_article(article: ArticleCreate, db: Session = Depends(get_db)):
    """创建文章"""
    db_article = Article(
        title=article.title,
        content=article.content,
        summary=article.summary,
        tags=article.tags,
        category=article.category,
        ai_generated=article.ai_generated,
        word_count=len(article.content),
        status="draft"
    )
    db.add(db_article)
    db.commit()
    db.refresh(db_article)
    return {"success": True, "id": db_article.id}

@app.put("/api/articles/{article_id}")
async def update_article(article_id: int, article: ArticleCreate, db: Session = Depends(get_db)):
    """更新文章"""
    db_article = db.query(Article).filter(Article.id == article_id).first()
    if not db_article:
        raise HTTPException(status_code=404, detail="文章不存在")
    db_article.title = article.title
    db_article.content = article.content
    db_article.summary = article.summary
    db_article.tags = article.tags
    db_article.category = article.category
    db_article.word_count = len(article.content)
    db_article.updated_at = datetime.now()
    db.commit()
    return {"success": True}

@app.delete("/api/articles/{article_id}")
async def delete_article(article_id: int, db: Session = Depends(get_db)):
    """删除文章"""
    db_article = db.query(Article).filter(Article.id == article_id).first()
    if not db_article:
        raise HTTPException(status_code=404, detail="文章不存在")
    db.delete(db_article)
    db.commit()
    return {"success": True}


# ---- 平台发布 ----

@app.get("/api/platforms")
async def get_platforms():
    """获取平台列表及登录状态"""
    return {"platforms": PublisherManager.get_platform_status()}

@app.post("/api/publish")
async def publish_article(req: PublishRequest, background_tasks: BackgroundTasks,
                           db: Session = Depends(get_db)):
    """发布文章到多个平台"""
    article = db.query(Article).filter(Article.id == req.article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")

    # 创建发布任务记录
    tasks = []
    for platform in req.platforms:
        task = PublishTask(
            article_id=req.article_id,
            platform=platform,
            status="pending",
            scheduled_at=datetime.fromisoformat(req.scheduled_at) if req.scheduled_at else None
        )
        db.add(task)
        tasks.append(task)
    db.commit()

    task_ids = [t.id for t in tasks]

    # 后台执行发布
    async def do_publish():
        tags = [t.strip() for t in (article.tags or "").split(",") if t.strip()]
        results = await PublisherManager.publish_to_platforms(
            article.title, article.content, req.platforms, tags, req.scheduled_at
        )
        # 更新任务状态
        db2 = SessionLocal()
        try:
            for i, platform in enumerate(req.platforms):
                result = results.get(platform, {})
                task = db2.query(PublishTask).filter(PublishTask.id == task_ids[i]).first()
                if task:
                    task.status = "success" if result.get("success") else "failed"
                    task.error_msg = result.get("error", "")
                    task.result_url = result.get("url", "")
                    if result.get("success"):
                        task.published_at = datetime.now()
                        # 更新文章状态
                        a = db2.query(Article).filter(Article.id == req.article_id).first()
                        if a:
                            a.status = "published"
            db2.commit()
        finally:
            db2.close()

    background_tasks.add_task(do_publish)

    return {
        "success": True,
        "message": f"已提交发布任务，正在发布到 {len(req.platforms)} 个平台",
        "task_ids": task_ids
    }

@app.get("/api/publish/tasks")
async def get_publish_tasks(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """获取发布任务列表"""
    tasks = db.query(PublishTask).order_by(PublishTask.created_at.desc()).offset(skip).limit(limit).all()
    total = db.query(PublishTask).count()
    return {
        "total": total,
        "tasks": [
            {
                "id": t.id,
                "article_id": t.article_id,
                "platform": t.platform,
                "platform_name": PLATFORM_CONFIGS.get(t.platform, {}).get("name", t.platform),
                "status": t.status,
                "error_msg": t.error_msg,
                "result_url": t.result_url,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "published_at": t.published_at.isoformat() if t.published_at else None,
            }
            for t in tasks
        ]
    }


# ---- AI 配置 ----

@app.get("/api/ai-configs")
async def list_ai_configs(db: Session = Depends(get_db)):
    """获取 AI 配置列表"""
    configs = db.query(AIConfig).all()
    return {
        "configs": [
            {
                "id": c.id,
                "name": c.name,
                "provider": c.provider,
                "model_name": c.model_name,
                "api_base": c.api_base,
                "is_default": c.is_default,
                "is_active": c.is_active,
                "api_key_set": bool(c.api_key),
            }
            for c in configs
        ]
    }

@app.post("/api/ai-configs")
async def create_ai_config(config: AIConfigCreate, db: Session = Depends(get_db)):
    """创建 AI 配置"""
    if config.is_default:
        db.query(AIConfig).update({"is_default": False})

    db_config = AIConfig(
        name=config.name,
        provider=config.provider,
        api_key=config.api_key,
        api_base=config.api_base,
        model_name=config.model_name,
        is_default=config.is_default,
        is_active=True
    )
    db.add(db_config)
    db.commit()
    db.refresh(db_config)
    return {"success": True, "id": db_config.id}

@app.delete("/api/ai-configs/{config_id}")
async def delete_ai_config(config_id: int, db: Session = Depends(get_db)):
    """删除 AI 配置"""
    config = db.query(AIConfig).filter(AIConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    db.delete(config)
    db.commit()
    return {"success": True}


# ---- 统计数据 ----

@app.get("/api/stats")
async def get_stats(db: Session = Depends(get_db)):
    """获取统计数据"""
    total_articles = db.query(Article).count()
    published_articles = db.query(Article).filter(Article.status == "published").count()
    draft_articles = db.query(Article).filter(Article.status == "draft").count()
    ai_articles = db.query(Article).filter(Article.ai_generated == True).count()

    total_tasks = db.query(PublishTask).count()
    success_tasks = db.query(PublishTask).filter(PublishTask.status == "success").count()
    failed_tasks = db.query(PublishTask).filter(PublishTask.status == "failed").count()

    # 平台发布分布
    platform_stats = {}
    for task in db.query(PublishTask).filter(PublishTask.status == "success").all():
        platform_name = PLATFORM_CONFIGS.get(task.platform, {}).get("name", task.platform)
        platform_stats[platform_name] = platform_stats.get(platform_name, 0) + 1

    return {
        "articles": {
            "total": total_articles,
            "published": published_articles,
            "draft": draft_articles,
            "ai_generated": ai_articles
        },
        "publish_tasks": {
            "total": total_tasks,
            "success": success_tasks,
            "failed": failed_tasks,
            "success_rate": round(success_tasks / total_tasks * 100, 1) if total_tasks > 0 else 0
        },
        "platform_distribution": platform_stats,
        "platforms_count": len(PLATFORM_CONFIGS),
        "supported_platforms": [
            {"platform": k, "name": v["name"], "icon": v.get("icon", "⚪")}
            for k, v in PLATFORM_CONFIGS.items()
        ]
    }

@app.get("/api/platform-styles")
async def get_platform_styles():
    """获取平台风格配置"""
    return {"styles": PLATFORM_STYLES}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
