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
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from loguru import logger

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from models import (
    init_db, get_db, Article, PublishTask, PlatformAccount,
    AIConfig, HotTopic, SessionLocal
)
from ai_creator import AICreator, PLATFORM_STYLES, MODEL_PROVIDERS, detect_provider
from publisher import PublisherManager, PLATFORM_CONFIGS
from content_moderator import moderate_content, run_moderation_pipeline, clean_markdown

# ===================== 初始化 =====================
BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(
    title="自媒体运营自动化平台",
    description="AI 驱动的多平台内容创作与发布管理系统",
    version="2.0.0"
)

# 启动时打印当前使用的模型
_detected = detect_provider()
logger.info(f"🤖 当前 AI 提供商：{_detected['name']} / {_detected['model']}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化数据库
init_db()

# 挂载前端静态资源
if (FRONTEND_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")


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

class ModerationRequest(BaseModel):
    title: str
    content: str
    platforms: Optional[List[str]] = None
    auto_rewrite: bool = True

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


# ===================== 前端路由 =====================

@app.get("/")
async def root():
    """根路径返回前端页面"""
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "自媒体运营自动化平台 API", "version": "2.0.0", "status": "running", "docs": "/docs"}

@app.get("/ui")
@app.get("/ui/{path:path}")
async def serve_frontend(path: str = ""):
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    raise HTTPException(status_code=404, detail="前端文件未找到")


# ===================== API 路由 =====================

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat(), "version": "2.0.0"}


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
    """生成完整文章（含自动 Qwen 审核 + 合规改写）"""
    try:
        creator = get_ai_creator(db)
        result = creator.generate_article(req.title, req.outline, req.platform, req.keywords)

        # ── 自动审核流水线（生成后立即审核）──────────────────────────────────
        logger.info(f"开始对生成内容进行审核: {result['title'][:30]}...")
        mod_result = run_moderation_pipeline(
            title=result["title"],
            content=result["content"],
            platforms=[req.platform] if req.platform != "general" else None,
            auto_rewrite=True,
        )
        # 使用审核后的内容（可能已被合规改写）
        final_content = mod_result.final_content or result["content"]
        moderation_summary = mod_result.to_dict()

        # 自动保存到草稿
        article = Article(
            title=result["title"],
            content=final_content,
            summary=result.get("summary", ""),
            tags="",
            category=result.get("platform_name", ""),
            ai_generated=True,
            word_count=len(final_content),
            status="draft"
        )
        db.add(article)
        db.commit()
        db.refresh(article)

        return {
            "success": True,
            "article_id": article.id,
            **result,
            "content": final_content,           # 覆盖为审核后内容
            "word_count": len(final_content),
            "moderation": moderation_summary,   # 附带审核报告
        }
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
    """一键适配多平台（深度差异化）"""
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

@app.get("/api/model-info")
async def get_model_info(db: Session = Depends(get_db)):
    """获取当前使用的 AI 模型信息"""
    creator = get_ai_creator(db)
    info = creator.get_current_model_info()
    return {
        "current": info,
        "providers": {
            k: {
                "name": v["name"],
                "default_model": v["default_model"],
                "models": v["models"],
                "price_note": v["price_note"],
                "env_key": v["env_key"],
                "configured": bool(os.environ.get(v["env_key"], "").strip())
            }
            for k, v in MODEL_PROVIDERS.items()
        }
    }

# ---- 内容审核 ----

@app.post("/api/moderate")
async def moderate_article(req: ModerationRequest):
    """
    独立内容审核接口（三层流水线）
    Layer 1: 本地敏感词检测
    Layer 2: Qwen 语义审核（需配置 DASHSCOPE_API_KEY）
    Layer 3: AI 合规改写（自动修复违规内容）
    """
    try:
        result = moderate_content(
            title=req.title,
            content=req.content,
            platforms=req.platforms,
            auto_rewrite=req.auto_rewrite,
        )
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"内容审核失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/moderate/quick")
async def quick_check(req: ModerationRequest):
    """
    快速敏感词检测（仅 Layer 1，无需 API Key，毫秒级）
    """
    from content_moderator import check_sensitive_words
    matches = check_sensitive_words(
        f"{req.title}\n\n{req.content}",
        req.platforms or ["all"]
    )
    return {
        "success": True,
        "is_clean": not any(m.severity == "block" for m in matches),
        "flagged_words": [m.to_dict() for m in matches],
        "block_count": sum(1 for m in matches if m.severity == "block"),
        "warn_count": sum(1 for m in matches if m.severity == "warn"),
    }


@app.get("/api/platform-styles")
async def get_platform_styles():
    """获取平台风格配置（含差异化说明）"""
    styles_info = {}
    for key, style in PLATFORM_STYLES.items():
        styles_info[key] = {
            "name": style["name"],
            "icon": style.get("icon", "⚪"),
            "style": style["style"],
            "tone": style.get("tone", ""),
            "max_length": style["max_length"],
            "min_length": style.get("min_length", 0),
            "format_hint": style["format_hint"],
            "special_rules": style.get("special_rules", []),
        }
    return {"styles": styles_info}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
