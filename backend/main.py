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
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
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
    user_requirement: Optional[str] = None   # 用户需求描述：我希望传达的内容是什么

class ImageSearchRequest(BaseModel):
    query: str                              # 搜索关键词
    count: int = 9                          # 返回图片数量

class ImageGenerateRequest(BaseModel):
    prompt: str                             # 用户描述的图片需求
    style: str = "realistic"               # realistic / illustration / anime / flat
    count: int = 4                          # 生成数量

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
async def generate_article(req: GenerateArticleRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """生成完整文章（审核改为异步后台执行，不阻塞返回）"""
    try:
        creator = get_ai_creator(db)
        result = creator.generate_article(
            req.title, req.outline, req.platform, req.keywords,
            user_requirement=req.user_requirement
        )

        # 先保存草稿，不等审核
        article = Article(
            title=result["title"],
            content=result["content"],
            summary=result.get("summary", ""),
            tags="",
            category=result.get("platform_name", ""),
            ai_generated=True,
            word_count=len(result["content"]),
            status="draft"
        )
        db.add(article)
        db.commit()
        db.refresh(article)
        article_id = article.id

        # 审核改为异步后台执行，不阻塞返回
        def run_moderation_bg():
            try:
                mod_result = run_moderation_pipeline(
                    title=result["title"],
                    content=result["content"],
                    platforms=[req.platform] if req.platform != "general" else None,
                    auto_rewrite=True,
                )
                final_content = mod_result.final_content or result["content"]
                # 审核完成后更新数据库
                with SessionLocal() as session:
                    art = session.get(Article, article_id)
                    if art:
                        art.content = final_content
                        art.word_count = len(final_content)
                        session.commit()
                logger.info(f"异步审核完成: article_id={article_id}, score={mod_result.to_dict().get('layer2', {}).get('score', 'N/A')}")
            except Exception as e:
                logger.warning(f"异步审核失败（不影响主流程）: {e}")

        background_tasks.add_task(run_moderation_bg)

        return {
            "success": True,
            "article_id": article_id,
            **result,
            "moderation": {"status": "pending", "message": "审核正在后台运行，不影响内容使用"},
        }
    except Exception as e:
        logger.error(f"生成文章失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ai/article/stream")
async def generate_article_stream(req: GenerateArticleRequest, db: Session = Depends(get_db)):
    """流式生成文章（SSE），内容逐块返回，用户体验最好"""
    import json
    creator = get_ai_creator(db)

    def event_stream():
        full_content = []
        try:
            for chunk in creator.generate_article_stream(
                req.title, req.outline, req.platform, req.keywords,
                user_requirement=req.user_requirement
            ):
                full_content.append(chunk)
                # SSE 格式：data: {...}\n\n
                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)}\n\n"

            # 流结束，发送完成事件（包含元数据）
            total_content = ''.join(full_content)
            style = PLATFORM_STYLES.get(req.platform, PLATFORM_STYLES["general"])
            summary = total_content[:120].replace('\n', ' ').strip() + '...'

            # 异步保存到数据库
            try:
                with SessionLocal() as session:
                    article = Article(
                        title=req.title,
                        content=total_content,
                        summary=summary,
                        tags="",
                        category=style["name"],
                        ai_generated=True,
                        word_count=len(total_content),
                        status="draft"
                    )
                    session.add(article)
                    session.commit()
                    session.refresh(article)
                    article_id = article.id
            except Exception as e:
                logger.error(f"保存文章失败: {e}")
                article_id = None

            yield f"data: {json.dumps({'type': 'done', 'article_id': article_id, 'title': req.title, 'summary': summary, 'word_count': len(total_content), 'platform': req.platform, 'platform_name': style['name']}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"流式生成失败: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        }
    )

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


# ---- 图片功能 ----

@app.post("/api/images/search")
async def search_images(req: ImageSearchRequest):
    """
    搜索网络图片（使用 Unsplash + Pexels 免费 API）
    优先返回高质量、可商用的图片
    """
    import httpx
    results = []

    # ── Unsplash（免费，高质量，无需 Key 可用演示端点）──────────────────────
    try:
        unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
        if unsplash_key:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.unsplash.com/search/photos",
                    params={"query": req.query, "per_page": req.count, "orientation": "landscape"},
                    headers={"Authorization": f"Client-ID {unsplash_key}"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for photo in data.get("results", []):
                        results.append({
                            "url": photo["urls"]["regular"],
                            "thumb": photo["urls"]["small"],
                            "source": "Unsplash",
                            "author": photo["user"]["name"],
                            "author_url": photo["user"]["links"]["html"],
                            "download_url": photo["urls"]["full"],
                            "alt": photo.get("alt_description") or req.query,
                            "width": photo["width"],
                            "height": photo["height"],
                        })
    except Exception as e:
        logger.warning(f"Unsplash 搜索失败: {e}")

    # ── Pexels（免费，高质量，需要 Key）────────────────────────────────────
    if len(results) < req.count:
        try:
            pexels_key = os.environ.get("PEXELS_API_KEY", "")
            if pexels_key:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        "https://api.pexels.com/v1/search",
                        params={"query": req.query, "per_page": req.count - len(results), "orientation": "landscape"},
                        headers={"Authorization": pexels_key}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for photo in data.get("photos", []):
                            results.append({
                                "url": photo["src"]["large"],
                                "thumb": photo["src"]["medium"],
                                "source": "Pexels",
                                "author": photo["photographer"],
                                "author_url": photo["photographer_url"],
                                "download_url": photo["src"]["original"],
                                "alt": photo.get("alt") or req.query,
                                "width": photo["width"],
                                "height": photo["height"],
                            })
        except Exception as e:
            logger.warning(f"Pexels 搜索失败: {e}")

    # ── Pixabay（免费，无版权，需要 Key）───────────────────────────────────
    if len(results) < req.count:
        try:
            pixabay_key = os.environ.get("PIXABAY_API_KEY", "")
            if pixabay_key:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        "https://pixabay.com/api/",
                        params={
                            "key": pixabay_key,
                            "q": req.query,
                            "per_page": req.count - len(results),
                            "image_type": "photo",
                            "orientation": "horizontal",
                            "safesearch": "true",
                            "lang": "zh"
                        }
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for hit in data.get("hits", []):
                            results.append({
                                "url": hit["webformatURL"],
                                "thumb": hit["previewURL"],
                                "source": "Pixabay",
                                "author": hit["user"],
                                "author_url": f"https://pixabay.com/users/{hit['user']}-{hit['user_id']}/",
                                "download_url": hit["largeImageURL"],
                                "alt": req.query,
                                "width": hit["imageWidth"],
                                "height": hit["imageHeight"],
                            })
        except Exception as e:
            logger.warning(f"Pixabay 搜索失败: {e}")

    # ── 兜底：Loremflickr（关键词匹配，无需 Key）+ Picsum Photos ──────────
    if not results:
        try:
            import urllib.parse
            keyword = urllib.parse.quote(req.query.replace(' ', ','))
            # Loremflickr 支持关键词，返回真实摄影图片
            loremflickr_results = []
            for i in range(min(req.count, 9)):
                w, h = 800, 600
                # 使用随机 lock 参数避免重复
                url = f"https://loremflickr.com/{w}/{h}/{keyword}?lock={i+1}"
                thumb_url = f"https://loremflickr.com/400/300/{keyword}?lock={i+1}"
                loremflickr_results.append({
                    "url": url,
                    "thumb": thumb_url,
                    "source": "Loremflickr（演示）",
                    "author": "Flickr Community",
                    "author_url": "https://loremflickr.com",
                    "download_url": url,
                    "alt": req.query,
                    "width": w,
                    "height": h,
                    "is_demo": True,
                })
            results.extend(loremflickr_results)
            logger.info(f"使用 Loremflickr 兜底，关键词: {req.query}")
        except Exception as e:
            logger.warning(f"Loremflickr 兜底失败: {e}")

    if not results:
        # 最终兜底：Picsum Photos（随机高质量图片）
        for i in range(min(req.count, 9)):
            pic_id = (hash(req.query) + i * 37) % 1000
            results.append({
                "url": f"https://picsum.photos/id/{pic_id}/800/600",
                "thumb": f"https://picsum.photos/id/{pic_id}/400/300",
                "source": "Picsum Photos（演示）",
                "author": "Picsum",
                "author_url": "https://picsum.photos",
                "download_url": f"https://picsum.photos/id/{pic_id}/1600/1200",
                "alt": req.query,
                "width": 800,
                "height": 600,
                "is_demo": True,
            })

    return {
        "success": True,
        "images": results[:req.count],
        "total": len(results),
        "has_demo": any(r.get("is_demo") for r in results[:req.count]),
        "demo_note": "当前使用演示图片，配置 UNSPLASH_ACCESS_KEY / PEXELS_API_KEY / PIXABAY_API_KEY 后可搜索真实图片" if any(r.get("is_demo") for r in results[:req.count]) else None,
    }


@app.post("/api/images/generate")
async def generate_image(req: ImageGenerateRequest):
    """
    AI 生成图片（使用 Gemini Imagen / DALL-E 3 / Stable Diffusion）
    优先使用 GEMINI_API_KEY，其次 OPENAI_API_KEY
    """
    import httpx

    style_prompts = {
        "realistic": "photorealistic, high quality, 8k, professional photography",
        "illustration": "digital illustration, flat design, colorful, modern",
        "anime": "anime style, manga, vibrant colors, detailed",
        "flat": "flat design, minimal, clean, vector style",
    }
    style_suffix = style_prompts.get(req.style, style_prompts["realistic"])
    full_prompt = f"{req.prompt}, {style_suffix}"

    # ── 优先使用 DALL-E 3（OpenAI）─────────────────────────────────────────
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_key:
        try:
            from openai import OpenAI as OAI
            client = OAI(api_key=openai_key, base_url="https://api.openai.com/v1")
            response = client.images.generate(
                model="dall-e-3",
                prompt=full_prompt,
                n=1,
                size="1792x1024",
                quality="standard",
            )
            images = [{"url": img.url, "source": "DALL-E 3", "revised_prompt": img.revised_prompt} for img in response.data]
            # 如果需要多张，循环生成
            for _ in range(min(req.count - 1, 3)):
                r2 = client.images.generate(model="dall-e-3", prompt=full_prompt, n=1, size="1792x1024", quality="standard")
                images.append({"url": r2.data[0].url, "source": "DALL-E 3", "revised_prompt": r2.data[0].revised_prompt})
            return {"success": True, "images": images, "model": "DALL-E 3", "prompt_used": full_prompt}
        except Exception as e:
            logger.warning(f"DALL-E 3 生成失败，尝试其他方案: {e}")

    # ── 备选：使用 DashScope Wanx（通义万象，国内可用）──────────────────────
    dashscope_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if dashscope_key:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # 提交任务
                resp = await client.post(
                    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
                    headers={"Authorization": f"Bearer {dashscope_key}", "X-DashScope-Async": "enable"},
                    json={
                        "model": "wanx2.1-t2i-turbo",
                        "input": {"prompt": req.prompt},
                        "parameters": {"size": "1440*960", "n": min(req.count, 4)}
                    }
                )
                if resp.status_code == 200:
                    task_id = resp.json()["output"]["task_id"]
                    # 轮询等待结果（最多 60 秒）
                    for _ in range(12):
                        await asyncio.sleep(5)
                        poll = await client.get(
                            f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}",
                            headers={"Authorization": f"Bearer {dashscope_key}"}
                        )
                        output = poll.json().get("output", {})
                        if output.get("task_status") == "SUCCEEDED":
                            images = [
                                {"url": r["url"], "source": "通义万象 Wanx"}
                                for r in output.get("results", [])
                            ]
                            return {"success": True, "images": images, "model": "通义万象 Wanx2.1", "prompt_used": req.prompt}
                        elif output.get("task_status") in ("FAILED", "CANCELED"):
                            break
        except Exception as e:
            logger.warning(f"通义万象生成失败: {e}")

    return {
        "success": False,
        "images": [],
        "message": "请配置 OPENAI_API_KEY（DALL-E 3）或 DASHSCOPE_API_KEY（通义万象）以启用 AI 图片生成"
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


# ===================== SEO 分析接口 =====================

class SEOAnalysisRequest(BaseModel):
    title: str
    content: str
    platform: str = "zhihu"  # 默认知乎，支持 zhihu/baijia/toutiao/wechat/bilibili


@app.post("/api/ai/seo-analysis")
async def seo_analysis(req: SEOAnalysisRequest, db: Session = Depends(get_db)):
    """对文章进行 SEO 分析，返回关键词布局、内外链建议、标题优化方案"""
    if not req.title or not req.content:
        raise HTTPException(status_code=400, detail="标题和内容不能为空")
    try:
        creator = get_ai_creator(db)
        result = creator.analyze_seo(req.title, req.content, req.platform)
        return {"success": True, "data": result, "platform": req.platform}
    except Exception as e:
        logger.error(f"SEO 分析失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===================== SEO 一键整改接口 =====================

class SEOFixRequest(BaseModel):
    title: str
    content: str
    platform: str = "zhihu"
    seo_result: dict  # 前端传入之前的 SEO 分析结果


@app.post("/api/ai/seo-fix")
async def seo_fix(req: SEOFixRequest, db: Session = Depends(get_db)):
    """根据 SEO 分析结果一键整改文章：自动嵌入关键词、优化标题、添加内外链提示"""
    if not req.title or not req.content:
        raise HTTPException(status_code=400, detail="标题和内容不能为空")
    try:
        creator = get_ai_creator(db)
        seo = req.seo_result

        # 构建整改指令
        primary_kws = ', '.join(seo.get('primary_keywords', []))
        longtail_kws = ', '.join(seo.get('long_tail_keywords', []))
        layout_tips = '\n'.join([f"- {k}: {v}" for k, v in seo.get('keyword_layout', {}).items()])
        improvement_tips = '\n'.join([f"- {t}" for t in seo.get('improvement_tips', [])])
        title_suggestions = seo.get('title_suggestions', [])
        best_title = title_suggestions[0] if title_suggestions else req.title
        internal_links = seo.get('internal_links', [])
        internal_topics = ', '.join([l.get('topic', '') for l in internal_links])

        system_prompt = f"""你是一个专业的 SEO 内容优化师。你的任务是根据具体的 SEO 分析建议，对文章进行精准优化。
要求：
1. 保持文章原有风格、结构和核心观点不变
2. 自然融入主关键词（{primary_kws}）和长尾词（{longtail_kws}）
3. 按照布局建议优化关键词位置
4. 在适当位置自然提及相关话题（{internal_topics}）以形成内容矩阵
5. 按照改进建议优化内容结构和可读性
6. 如果标题不够 SEO 友好，优先使用建议标题：{best_title}"""

        user_prompt = f"""请根据以下 SEO 分析建议，对文章进行优化整改。

当前标题：{req.title}
当前内容：
{req.content}

关键词布局建议：
{layout_tips}

改进建议：
{improvement_tips}

请输出两部分：
1. 优化后的标题（如果需要修改）
2. 优化后的完整正文

输出格式：
标题：[SEO优化后的标题]
---
[SEO优化后的完整正文]"""

        result_text = creator._chat(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_prompt}],
            temperature=0.6,
            max_tokens=4000
        )

        # 解析输出格式
        lines = result_text.strip().split('\n')
        new_title = req.title
        new_content = result_text

        for i, line in enumerate(lines):
            if line.startswith('标题：') or line.startswith('标题:'):
                new_title = line.split('：', 1)[-1].split(':', 1)[-1].strip().strip('[]')
            if line.strip() == '---':
                new_content = '\n'.join(lines[i+1:]).strip()
                break

        return {
            "success": True,
            "new_title": new_title,
            "new_content": new_content,
            "original_title": req.title,
            "platform": req.platform
        }
    except Exception as e:
        logger.error(f"SEO 整改失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===================== 选题助手接口 =====================

class TopicSuggestionRequest(BaseModel):
    category: str = "科技"
    platform: str = "general"
    include_history: bool = True  # 是否结合用户历史文章分析


@app.post("/api/ai/topic-suggestions")
async def get_topic_suggestions(req: TopicSuggestionRequest, db: Session = Depends(get_db)):
    """选题助手：结合热点趋势 + 用户历史内容，推荐高潜力选题和爆款标题"""
    history_titles = []
    if req.include_history:
        # 获取用户最近20篇文章标题作为历史参考
        try:
            articles = db.query(Article).order_by(Article.created_at.desc()).limit(20).all()
            history_titles = [a.title for a in articles if a.title]
        except Exception:
            history_titles = []

    try:
        creator = get_ai_creator(db)
        result = creator.generate_topic_suggestions(
            category=req.category,
            history_titles=history_titles,
            platform=req.platform
        )
        return {
            "success": True,
            "data": result,
            "history_count": len(history_titles),
            "category": req.category,
            "platform": req.platform
        }
    except Exception as e:
        logger.error(f"选题助手失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)


# ===================== 自然语言查询（NL → SQL）=====================
from nl_query import NLQueryEngine, PRESET_QUESTIONS

class NLQueryRequest(BaseModel):
    question: str
    use_mock: bool = False   # 强制使用演示数据（用于前端预览）

_nl_engine: Optional[NLQueryEngine] = None

def get_nl_engine(db: Session) -> NLQueryEngine:
    global _nl_engine
    if _nl_engine is None:
        config = db.query(AIConfig).filter(AIConfig.is_default == True, AIConfig.is_active == True).first()
        if config:
            _nl_engine = NLQueryEngine(api_key=config.api_key, api_base=config.api_base, model=config.model_name)
        else:
            _nl_engine = NLQueryEngine()
    return _nl_engine

@app.post("/api/analytics/query")
async def nl_query(req: NLQueryRequest, db: Session = Depends(get_db)):
    """
    自然语言查询运营数据
    输入：自然语言问题
    输出：SQL、数据表格、图表配置
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")
    try:
        engine = get_nl_engine(db)
        result = engine.query(req.question.strip(), use_mock=req.use_mock)
        return result
    except Exception as e:
        logger.error(f"NL 查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analytics/presets")
async def get_preset_questions():
    """获取预设问题模板"""
    return {"presets": PRESET_QUESTIONS}

@app.get("/api/analytics/db-schema")
async def get_db_schema():
    """获取数据库表结构（用于前端展示）"""
    from nl_query import DB_SCHEMA
    return {"schema": DB_SCHEMA}


# ===================== 图像生成增强（接入 DeepSeek + Pollinations）=====================

class ImageGenerateV2Request(BaseModel):
    prompt: str
    style: str = "realistic"
    count: int = 2
    article_title: Optional[str] = None   # 可选：根据文章标题自动优化 prompt
    platform: Optional[str] = None        # 可选：根据平台优化图片尺寸和风格

@app.post("/api/images/generate/v2")
async def generate_image_v2(req: ImageGenerateV2Request, db: Session = Depends(get_db)):
    """
    增强版图像生成接口
    1. 若提供 article_title，先用 AI 优化 prompt
    2. 优先使用 DALL-E 3（OpenAI）
    3. 备选：通义万象（DashScope）
    4. 兜底：Pollinations.ai（免费，无需 API Key）
    """
    import httpx

    final_prompt = req.prompt.strip()

    # Step 1: AI 优化 prompt（若提供文章标题）
    if req.article_title and not final_prompt:
        try:
            creator = get_ai_creator(db)
            platform_style_hint = {
                "xiaohongshu": "小红书风格，明亮清新，有生活感，适合种草",
                "wechat": "微信公众号风格，简洁专业，信息清晰",
                "zhihu": "知乎风格，专业严肃，数据可视化风格",
                "toutiao": "今日头条风格，吸引眼球，有冲击力",
                "bilibili": "B站风格，年轻活力，有趣生动",
                "weibo": "微博风格，热点感强，视觉冲击",
                "douyin": "抖音风格，极简有力，视觉冲击强",
            }.get(req.platform or "", "通用自媒体风格，专业美观")

            optimize_prompt = f"""请为以下文章生成一段英文图片描述（用于 AI 图像生成），要求：
1. 描述要具体，包含主题、场景、色调、风格
2. 风格要求：{platform_style_hint}
3. 图片类型：文章配图/封面图
4. 只输出英文描述，不要任何解释，长度 50-100 词

文章标题：{req.article_title}"""
            final_prompt = creator._chat(
                [{"role": "user", "content": optimize_prompt}],
                temperature=0.7, max_tokens=200
            )
            logger.info(f"AI 优化后的 prompt: {final_prompt}")
        except Exception as e:
            logger.warning(f"Prompt 优化失败，使用原始标题: {e}")
            final_prompt = req.article_title

    if not final_prompt:
        raise HTTPException(status_code=400, detail="请提供图片描述或文章标题")

    style_prompts = {
        "realistic": "photorealistic, high quality, 8k, professional photography, sharp focus",
        "illustration": "digital illustration, flat design, colorful, modern, clean lines",
        "anime": "anime style, manga, vibrant colors, detailed, Studio Ghibli inspired",
        "flat": "flat design, minimal, clean, vector style, geometric shapes",
        "cinematic": "cinematic, dramatic lighting, film grain, wide angle, epic",
    }
    style_suffix = style_prompts.get(req.style, style_prompts["realistic"])
    full_prompt = f"{final_prompt}, {style_suffix}"

    # 平台尺寸映射
    platform_sizes = {
        "xiaohongshu": "1024x1024",   # 正方形
        "wechat": "1792x1024",         # 横版
        "zhihu": "1792x1024",
        "toutiao": "1792x1024",
        "bilibili": "1792x1024",
        "weibo": "1024x1024",
        "douyin": "1024x1792",         # 竖版
    }
    image_size = platform_sizes.get(req.platform or "", "1792x1024")

    # ── 方案 0：Nano Banana（Gemini 图像生成，性价比最高）────────────────────
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    gemini_base = os.environ.get("GEMINI_IMAGE_BASE_URL", "").strip()
    if gemini_key:
        try:
            import httpx as _httpx
            # 支持自定义中转地址（如 API易、云雾等），默认使用 Google 官方
            base_url = gemini_base or "https://generativelanguage.googleapis.com/v1beta"
            # 平台尺寸映射到 Gemini 支持的尺寸
            gemini_size_map = {
                "1024x1024": "1:1",
                "1024x1792": "9:16",
                "1792x1024": "16:9",
            }
            aspect_ratio = gemini_size_map.get(image_size, "16:9")
            images = []
            count = min(req.count, 4)
            async with _httpx.AsyncClient(timeout=60) as hc:
                resp = await hc.post(
                    f"{base_url}/models/gemini-2.0-flash-preview-image-generation:generateContent",
                    params={"key": gemini_key},
                    json={
                        "contents": [{"parts": [{"text": full_prompt}]}],
                        "generationConfig": {
                            "responseModalities": ["TEXT", "IMAGE"],
                            "numberOfImages": count,
                        }
                    }
                )
            if resp.status_code == 200:
                rj = resp.json()
                for candidate in rj.get("candidates", []):
                    for part in candidate.get("content", {}).get("parts", []):
                        if "inlineData" in part:
                            b64 = part["inlineData"]["data"]
                            mime = part["inlineData"].get("mimeType", "image/png")
                            images.append({
                                "url": f"data:{mime};base64,{b64}",
                                "source": "Nano Banana（Gemini）",
                                "optimized_prompt": final_prompt,
                            })
                if images:
                    return {
                        "success": True,
                        "images": images,
                        "model": "Nano Banana（Gemini 2.0 Flash Image）",
                        "prompt_used": full_prompt,
                        "optimized_prompt": final_prompt,
                    }
            else:
                logger.warning(f"Nano Banana 失败: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"Nano Banana（Gemini）失败: {e}")

    # ── 方案 0b：Nano Banana via OpenAI-compatible API（中转站）────────────────
    # 当 GEMINI_IMAGE_API_KEY 配置时，通过 OpenAI 兼容接口调用 Gemini 图像生成
    gemini_compat_key = os.environ.get("GEMINI_IMAGE_API_KEY", "").strip()
    gemini_compat_base = os.environ.get("GEMINI_IMAGE_API_BASE", "").strip()
    if gemini_compat_key and gemini_compat_base:
        try:
            from openai import OpenAI as OAI
            client = OAI(api_key=gemini_compat_key, base_url=gemini_compat_base)
            images = []
            for _ in range(min(req.count, 4)):
                r = client.images.generate(
                    model="gemini-2.5-flash-image",
                    prompt=full_prompt,
                    n=1,
                    size=image_size,
                )
                images.append({
                    "url": r.data[0].url,
                    "source": "Nano Banana（Gemini）",
                    "optimized_prompt": final_prompt,
                })
            if images:
                return {
                    "success": True,
                    "images": images,
                    "model": "Nano Banana（Gemini 2.5 Flash Image）",
                    "prompt_used": full_prompt,
                    "optimized_prompt": final_prompt,
                }
        except Exception as e:
            logger.warning(f"Nano Banana（中转）失败: {e}")

    # ── 方案 1：DALL-E 3（OpenAI）─────────────────────────────────────────
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    openai_base = os.environ.get("OPENAI_IMAGE_BASE_URL", os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).strip()
    if openai_key:
        try:
            from openai import OpenAI as OAI
            client = OAI(api_key=openai_key, base_url=openai_base)
            images = []
            for _ in range(min(req.count, 4)):
                r = client.images.generate(
                    model="dall-e-3",
                    prompt=full_prompt,
                    n=1,
                    size=image_size,
                    quality="standard",
                )
                images.append({
                    "url": r.data[0].url,
                    "source": "DALL-E 3",
                    "revised_prompt": r.data[0].revised_prompt,
                    "optimized_prompt": final_prompt,
                })
            return {
                "success": True,
                "images": images,
                "model": "DALL-E 3",
                "prompt_used": full_prompt,
                "optimized_prompt": final_prompt,
            }
        except Exception as e:
            logger.warning(f"DALL-E 3 失败: {e}")

    # ── 方案 2：通义万象（DashScope）────────────────────────────────────────
    dashscope_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if dashscope_key:
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
                    headers={"Authorization": f"Bearer {dashscope_key}", "X-DashScope-Async": "enable"},
                    json={
                        "model": "wanx2.1-t2i-turbo",
                        "input": {"prompt": req.prompt},
                        "parameters": {"size": "1440*960", "n": min(req.count, 4)}
                    }
                )
                if resp.status_code == 200:
                    task_id = resp.json()["output"]["task_id"]
                    for _ in range(18):
                        await asyncio.sleep(5)
                        poll = await client.get(
                            f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}",
                            headers={"Authorization": f"Bearer {dashscope_key}"}
                        )
                        output = poll.json().get("output", {})
                        if output.get("task_status") == "SUCCEEDED":
                            images = [
                                {"url": r["url"], "source": "通义万象", "optimized_prompt": final_prompt}
                                for r in output.get("results", [])
                            ]
                            return {
                                "success": True,
                                "images": images,
                                "model": "通义万象 Wanx2.1",
                                "prompt_used": req.prompt,
                                "optimized_prompt": final_prompt,
                            }
                        elif output.get("task_status") in ("FAILED", "CANCELED"):
                            break
        except Exception as e:
            logger.warning(f"通义万象失败: {e}")

    # ── 方案 3：Loremflickr（关键词匹配真实图片，无需 Key）───────────────
    try:
        import urllib.parse
        # 提取关键词：优先使用用户原始描述，如果是英文则直接使用
        kw_source = req.prompt if req.prompt else final_prompt
        # 取前三个单词作为关键词
        kw_words = [w for w in kw_source.replace(',', ' ').split() if len(w) > 2][:3]
        keyword = ','.join(kw_words) if kw_words else 'technology'
        keyword_enc = urllib.parse.quote(keyword)
        w_px, h_px = (1024, 1024) if image_size == "1024x1024" else \
                     (1024, 1792) if image_size == "1024x1792" else (1792, 1024)
        images = []
        for i in range(min(req.count, 4)):
            url = f"https://loremflickr.com/{w_px}/{h_px}/{keyword_enc}?lock={i+10}"
            images.append({
                "url": url,
                "thumb": f"https://loremflickr.com/400/300/{keyword_enc}?lock={i+10}",
                "source": "Loremflickr（演示）",
                "optimized_prompt": final_prompt,
                "is_demo": True,
            })
        return {
            "success": True,
            "images": images,
            "model": "Loremflickr（演示）",
            "prompt_used": full_prompt,
            "optimized_prompt": final_prompt,
            "note": "当前使用演示图片（Loremflickr），配置 OPENAI_API_KEY 可升级为 DALL-E 3 真实 AI 生成",
        }
    except Exception as e:
        logger.error(f"Loremflickr 失败: {e}")

    # ── 最终兜底：Picsum Photos（随机高质量图片）────────────────────
    try:
        images = []
        for i in range(min(req.count, 4)):
            pic_id = (abs(hash(final_prompt)) + i * 37) % 1000
            images.append({
                "url": f"https://picsum.photos/id/{pic_id}/800/600",
                "thumb": f"https://picsum.photos/id/{pic_id}/400/300",
                "source": "Picsum Photos（演示）",
                "optimized_prompt": final_prompt,
                "is_demo": True,
            })
        return {
            "success": True,
            "images": images,
            "model": "Picsum Photos（演示）",
            "prompt_used": full_prompt,
            "optimized_prompt": final_prompt,
            "note": "当前使用演示图片，配置 OPENAI_API_KEY 可升级为 DALL-E 3 真实 AI 生成",
        }
    except Exception as e:
        logger.error(f"Picsum 失败: {e}")

    return {
        "success": False,
        "images": [],
        "message": "所有图像生成方案均失败，请检查网络连接或配置 API Key",
    }



# ===================== 系统日志 API =====================

# 内存日志缓冲（最近 200 条）
import collections
_log_buffer: collections.deque = collections.deque(maxlen=200)

class _BufferSink:
    """loguru sink：将日志同时写入内存缓冲"""
    def write(self, message):
        record = message.record
        _log_buffer.append({
            "time": record["time"].strftime("%Y-%m-%d %H:%M:%S"),
            "level": record["level"].name,
            "module": record["name"],
            "message": record["message"],
        })
    def __call__(self, message):
        self.write(message)

# 注册内存 sink
logger.add(_BufferSink(), level="DEBUG", format="{message}")


@app.get("/api/logs")
async def get_logs(
    level: Optional[str] = None,
    module: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """获取系统日志（内存缓冲 + 数据库）"""
    # 先从内存缓冲取最新日志
    mem_logs = list(reversed(list(_log_buffer)))
    if level:
        mem_logs = [l for l in mem_logs if l["level"] == level.upper()]
    if module:
        mem_logs = [l for l in mem_logs if module.lower() in l["module"].lower()]
    mem_logs = mem_logs[:limit]

    # 同时从数据库取持久化日志
    try:
        from models import SystemLog
        q = db.query(SystemLog).order_by(SystemLog.created_at.desc())
        if level:
            q = q.filter(SystemLog.level == level.upper())
        if module:
            q = q.filter(SystemLog.module.contains(module))
        db_logs = [
            {
                "time": l.created_at.strftime("%Y-%m-%d %H:%M:%S") if l.created_at else "",
                "level": l.level or "INFO",
                "module": l.module or "system",
                "message": l.message or "",
                "source": "db",
            }
            for l in q.limit(limit).all()
        ]
    except Exception:
        db_logs = []

    # 合并去重（内存优先）
    combined = mem_logs[:limit]
    return {
        "success": True,
        "logs": combined,
        "db_logs": db_logs[:50],
        "total": len(combined),
    }


@app.delete("/api/logs")
async def clear_logs():
    """清空内存日志缓冲"""
    _log_buffer.clear()
    return {"success": True, "message": "日志已清空"}


# ===================== 图片 API Key 配置接口 =====================

class ImageAPIKeyRequest(BaseModel):
    unsplash_key: Optional[str] = None
    pexels_key: Optional[str] = None
    pixabay_key: Optional[str] = None


@app.post("/api/settings/image-keys")
async def save_image_api_keys(req: ImageAPIKeyRequest):
    """
    保存图片搜索 API Key（写入进程环境变量，重启后失效）
    生产环境建议通过 Railway / Docker 环境变量持久化
    """
    updated = []
    if req.unsplash_key is not None:
        os.environ["UNSPLASH_ACCESS_KEY"] = req.unsplash_key.strip()
        updated.append("Unsplash")
    if req.pexels_key is not None:
        os.environ["PEXELS_API_KEY"] = req.pexels_key.strip()
        updated.append("Pexels")
    if req.pixabay_key is not None:
        os.environ["PIXABAY_API_KEY"] = req.pixabay_key.strip()
        updated.append("Pixabay")
    logger.info(f"图片搜索 API Key 已更新: {', '.join(updated) if updated else '无变更'}")
    return {
        "success": True,
        "updated": updated,
        "message": f"已更新 {len(updated)} 个 Key，立即生效（重启后需重新配置，建议写入环境变量）",
        "status": {
            "unsplash": bool(os.environ.get("UNSPLASH_ACCESS_KEY")),
            "pexels": bool(os.environ.get("PEXELS_API_KEY")),
            "pixabay": bool(os.environ.get("PIXABAY_API_KEY")),
        }
    }


@app.get("/api/settings/image-keys")
async def get_image_api_key_status():
    """获取图片搜索 API Key 配置状态"""
    return {
        "success": True,
        "status": {
            "unsplash": {
                "configured": bool(os.environ.get("UNSPLASH_ACCESS_KEY")),
                "name": "Unsplash",
                "description": "高质量摄影图库，每小时 50 次免费请求",
                "apply_url": "https://unsplash.com/developers",
                "env_key": "UNSPLASH_ACCESS_KEY",
            },
            "pexels": {
                "configured": bool(os.environ.get("PEXELS_API_KEY")),
                "name": "Pexels",
                "description": "免费商用图库，无版权限制，每月 25000 次请求",
                "apply_url": "https://www.pexels.com/api/",
                "env_key": "PEXELS_API_KEY",
            },
            "pixabay": {
                "configured": bool(os.environ.get("PIXABAY_API_KEY")),
                "name": "Pixabay",
                "description": "CC0 授权图库，每小时 100 次免费请求",
                "apply_url": "https://pixabay.com/api/docs/",
                "env_key": "PIXABAY_API_KEY",
            },
        },
        "note": "至少配置一个 Key 即可启用真实图片搜索，未配置时使用演示图片",
    }


# ===================== AI 生图 API Key 配置接口 =====================

class AIImageKeyRequest(BaseModel):
    gemini_image_api_key: Optional[str] = None   # Nano Banana（Gemini）中转 Key
    gemini_image_api_base: Optional[str] = None  # Nano Banana 中转 Base URL
    openai_api_key: Optional[str] = None         # DALL-E 3 Key
    openai_image_base_url: Optional[str] = None  # DALL-E 3 中转 Base URL
    dashscope_api_key: Optional[str] = None      # 通义万象 Key


@app.post("/api/settings/ai-image-keys")
async def save_ai_image_keys(req: AIImageKeyRequest):
    """
    保存 AI 生图 API Key（写入进程环境变量，重启后失效）
    生产环境建议通过 Railway / Docker 环境变量持久化
    """
    updated = []
    if req.gemini_image_api_key is not None:
        os.environ["GEMINI_IMAGE_API_KEY"] = req.gemini_image_api_key.strip()
        updated.append("Nano Banana API Key")
    if req.gemini_image_api_base is not None:
        os.environ["GEMINI_IMAGE_API_BASE"] = req.gemini_image_api_base.strip()
        updated.append("Nano Banana API Base URL")
    if req.openai_api_key is not None:
        os.environ["OPENAI_API_KEY"] = req.openai_api_key.strip()
        updated.append("DALL-E 3 API Key")
    if req.openai_image_base_url is not None:
        os.environ["OPENAI_IMAGE_BASE_URL"] = req.openai_image_base_url.strip()
        updated.append("DALL-E 3 Base URL")
    if req.dashscope_api_key is not None:
        os.environ["DASHSCOPE_API_KEY"] = req.dashscope_api_key.strip()
        updated.append("通义万象 API Key")
    logger.info(f"AI 生图 API Key 已更新: {', '.join(updated) if updated else '无变更'}")
    return {
        "success": True,
        "updated": updated,
        "message": f"已更新 {len(updated)} 项配置，立即生效（重启后需重新配置，建议写入 Railway 环境变量）",
    }


@app.get("/api/settings/ai-image-keys")
async def get_ai_image_key_status():
    """获取 AI 生图 API Key 配置状态"""
    return {
        "success": True,
        "status": {
            "nano_banana": {
                "configured": bool(os.environ.get("GEMINI_IMAGE_API_KEY")),
                "name": "🍌 Nano Banana（Gemini）",
                "description": "Google Gemini 图像生成，$0.02/张，性价比最高，推荐首选",
                "env_key": "GEMINI_IMAGE_API_KEY",
                "base_url": os.environ.get("GEMINI_IMAGE_API_BASE", ""),
                "base_url_env": "GEMINI_IMAGE_API_BASE",
                "apply_url": "https://apiyi.com",
                "price": "$0.02/张",
            },
            "dalle3": {
                "configured": bool(os.environ.get("OPENAI_API_KEY")),
                "name": "🎨 DALL-E 3（OpenAI）",
                "description": "OpenAI 官方图像生成，$0.04/张，细节丰富，支持中转",
                "env_key": "OPENAI_API_KEY",
                "base_url": os.environ.get("OPENAI_IMAGE_BASE_URL", os.environ.get("OPENAI_BASE_URL", "")),
                "base_url_env": "OPENAI_IMAGE_BASE_URL",
                "apply_url": "https://apiyi.com",
                "price": "$0.04/张",
            },
            "wanx": {
                "configured": bool(os.environ.get("DASHSCOPE_API_KEY")),
                "name": "🖌️ 通义万象（阿里云）",
                "description": "阿里云文生图，中文 prompt 友好，有免费额度",
                "env_key": "DASHSCOPE_API_KEY",
                "base_url": "",
                "base_url_env": "",
                "apply_url": "https://dashscope.console.aliyun.com/apiKey",
                "price": "有免费额度",
            },
        },
        "priority": "Nano Banana → DALL-E 3 → 通义万象 → Loremflickr（演示兜底）",
    }
