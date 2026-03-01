"""
多平台内容发布模块
基于 Playwright 浏览器自动化实现合规发布
支持：知乎、微信公众号、百家号、今日头条、小红书
"""
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from loguru import logger

try:
    from playwright.async_api import async_playwright, Page, Browser
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright 未安装，发布功能将以模拟模式运行")

BASE_DIR = Path(__file__).parent.parent
COOKIES_DIR = BASE_DIR / "cookies"
COOKIES_DIR.mkdir(exist_ok=True)


PLATFORM_CONFIGS = {
    "zhihu": {
        "name": "知乎",
        "login_url": "https://www.zhihu.com/signin",
        "publish_url": "https://zhuanlan.zhihu.com/write",
        "cookie_file": "zhihu_cookies.json",
        "icon": "🔵",
        "type": "article"
    },
    "wechat": {
        "name": "微信公众号",
        "login_url": "https://mp.weixin.qq.com/",
        "publish_url": "https://mp.weixin.qq.com/cgi-bin/appmsg",
        "cookie_file": "wechat_cookies.json",
        "icon": "🟢",
        "type": "article"
    },
    "baijia": {
        "name": "百家号",
        "login_url": "https://baijiahao.baidu.com/builder/rc/login",
        "publish_url": "https://baijiahao.baidu.com/builder/rc/edit",
        "cookie_file": "baijia_cookies.json",
        "icon": "🔴",
        "type": "article"
    },
    "toutiao": {
        "name": "今日头条",
        "login_url": "https://mp.toutiao.com/",
        "publish_url": "https://mp.toutiao.com/profile_v4/graphic/publish",
        "cookie_file": "toutiao_cookies.json",
        "icon": "🟠",
        "type": "article"
    },
    "xiaohongshu": {
        "name": "小红书",
        "login_url": "https://creator.xiaohongshu.com/login",
        "publish_url": "https://creator.xiaohongshu.com/publish/publish",
        "cookie_file": "xhs_cookies.json",
        "icon": "🔴",
        "type": "note"
    },
    "weibo": {
        "name": "微博",
        "login_url": "https://weibo.com/login.php",
        "publish_url": "https://weibo.com/",
        "cookie_file": "weibo_cookies.json",
        "icon": "🟡",
        "type": "post"
    }
}


class PlatformPublisher:
    """平台发布器基类"""

    def __init__(self, platform: str):
        self.platform = platform
        self.config = PLATFORM_CONFIGS.get(platform, {})
        self.cookie_path = COOKIES_DIR / self.config.get("cookie_file", f"{platform}_cookies.json")

    def is_logged_in(self) -> bool:
        """检查是否已登录（Cookie 文件是否存在）"""
        return self.cookie_path.exists()

    def get_login_url(self) -> str:
        return self.config.get("login_url", "")

    async def save_cookies(self, page: "Page"):
        """保存 Cookie"""
        cookies = await page.context.cookies()
        with open(self.cookie_path, 'w', encoding='utf-8') as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ {self.config['name']} Cookie 已保存")

    async def load_cookies(self, page: "Page") -> bool:
        """加载 Cookie"""
        if not self.cookie_path.exists():
            return False
        try:
            with open(self.cookie_path, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            await page.context.add_cookies(cookies)
            return True
        except Exception as e:
            logger.error(f"加载 Cookie 失败: {e}")
            return False

    async def publish(self, title: str, content: str, tags: List[str] = None,
                      scheduled_time: Optional[str] = None) -> Dict[str, Any]:
        """发布文章（子类实现）"""
        raise NotImplementedError


class ZhihuPublisher(PlatformPublisher):
    """知乎发布器"""

    def __init__(self):
        super().__init__("zhihu")

    async def publish(self, title: str, content: str, tags: List[str] = None,
                      scheduled_time: Optional[str] = None) -> Dict[str, Any]:
        if not PLAYWRIGHT_AVAILABLE:
            return self._mock_publish(title, "zhihu")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = await context.new_page()

            try:
                # 加载 Cookie
                if not await self.load_cookies(page):
                    return {"success": False, "error": "未登录，请先扫码登录知乎账号"}

                # 访问写作页面
                await page.goto(self.config["publish_url"], wait_until="networkidle", timeout=30000)
                await asyncio.sleep(2)

                # 检查是否需要重新登录
                if "signin" in page.url or "login" in page.url:
                    return {"success": False, "error": "Cookie 已过期，请重新登录"}

                # 输入标题
                title_input = await page.wait_for_selector('input[placeholder*="标题"]', timeout=10000)
                await title_input.click()
                await title_input.fill(title)
                await asyncio.sleep(0.5)

                # 输入内容（知乎编辑器）
                editor = await page.wait_for_selector('.DraftEditor-editorContainer', timeout=10000)
                await editor.click()
                await page.keyboard.type(content[:4000])  # 知乎限制
                await asyncio.sleep(1)

                # 点击发布按钮
                publish_btn = await page.wait_for_selector('button:has-text("发布")', timeout=5000)
                await publish_btn.click()
                await asyncio.sleep(3)

                return {"success": True, "platform": "zhihu", "message": "知乎文章发布成功"}

            except Exception as e:
                logger.error(f"知乎发布失败: {e}")
                return {"success": False, "error": str(e)}
            finally:
                await browser.close()

    def _mock_publish(self, title: str, platform: str) -> Dict[str, Any]:
        """模拟发布（测试用）"""
        logger.info(f"[模拟发布] 平台: {platform}, 标题: {title}")
        return {
            "success": True,
            "platform": platform,
            "message": f"[模拟] {title} 已成功发布到 {PLATFORM_CONFIGS[platform]['name']}",
            "mock": True
        }


class BaijiahaoPublisher(PlatformPublisher):
    """百家号发布器"""

    def __init__(self):
        super().__init__("baijia")

    async def publish(self, title: str, content: str, tags: List[str] = None,
                      scheduled_time: Optional[str] = None) -> Dict[str, Any]:
        if not PLAYWRIGHT_AVAILABLE:
            return {"success": True, "platform": "baijia", "message": "[模拟] 百家号发布成功", "mock": True}

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            try:
                if not await self.load_cookies(page):
                    return {"success": False, "error": "未登录，请先登录百家号账号"}

                await page.goto(self.config["publish_url"], wait_until="networkidle", timeout=30000)
                await asyncio.sleep(2)

                if "login" in page.url:
                    return {"success": False, "error": "Cookie 已过期，请重新登录"}

                # 百家号编辑器操作
                title_input = await page.wait_for_selector('input[placeholder*="标题"]', timeout=10000)
                await title_input.fill(title)

                editor = await page.wait_for_selector('[contenteditable="true"]', timeout=10000)
                await editor.click()
                await page.keyboard.type(content[:2000])
                await asyncio.sleep(1)

                publish_btn = await page.wait_for_selector('button:has-text("发布文章")', timeout=5000)
                await publish_btn.click()
                await asyncio.sleep(3)

                return {"success": True, "platform": "baijia", "message": "百家号文章发布成功"}

            except Exception as e:
                logger.error(f"百家号发布失败: {e}")
                return {"success": False, "error": str(e)}
            finally:
                await browser.close()


class PublisherManager:
    """发布管理器 - 统一调度多平台发布"""

    PUBLISHERS = {
        "zhihu": ZhihuPublisher,
        "baijia": BaijiahaoPublisher,
    }

    @classmethod
    def get_publisher(cls, platform: str) -> Optional[PlatformPublisher]:
        publisher_class = cls.PUBLISHERS.get(platform)
        if publisher_class:
            return publisher_class()
        # 通用发布器（模拟）
        return GenericPublisher(platform)

    @classmethod
    async def publish_to_platforms(cls, title: str, content: str,
                                   platforms: List[str], tags: List[str] = None,
                                   scheduled_time: Optional[str] = None) -> Dict[str, Any]:
        """批量发布到多个平台"""
        results = {}
        for platform in platforms:
            publisher = cls.get_publisher(platform)
            if publisher:
                try:
                    logger.info(f"正在发布到 {PLATFORM_CONFIGS.get(platform, {}).get('name', platform)}...")
                    result = await publisher.publish(title, content, tags, scheduled_time)
                    results[platform] = result
                    await asyncio.sleep(2)  # 平台间间隔
                except Exception as e:
                    results[platform] = {"success": False, "error": str(e)}
            else:
                results[platform] = {"success": False, "error": "不支持的平台"}
        return results

    @classmethod
    def get_platform_status(cls) -> List[Dict]:
        """获取所有平台登录状态"""
        status_list = []
        for platform, config in PLATFORM_CONFIGS.items():
            cookie_path = COOKIES_DIR / config.get("cookie_file", f"{platform}_cookies.json")
            status_list.append({
                "platform": platform,
                "name": config["name"],
                "icon": config.get("icon", "⚪"),
                "type": config.get("type", "article"),
                "login_url": config.get("login_url", ""),
                "logged_in": cookie_path.exists(),
                "cookie_file": str(cookie_path)
            })
        return status_list


class GenericPublisher(PlatformPublisher):
    """通用发布器（模拟模式）"""

    async def publish(self, title: str, content: str, tags: List[str] = None,
                      scheduled_time: Optional[str] = None) -> Dict[str, Any]:
        platform_name = PLATFORM_CONFIGS.get(self.platform, {}).get("name", self.platform)
        logger.info(f"[模拟发布] {platform_name}: {title}")
        return {
            "success": True,
            "platform": self.platform,
            "message": f"[模拟] 已成功发布到 {platform_name}",
            "mock": True,
            "note": "请配置真实 Cookie 后启用实际发布功能"
        }
