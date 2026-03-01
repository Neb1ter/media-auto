"""
AI 内容创作模块
支持 OpenAI / 通义千问 / 文心一言 等多模型
"""
import os
import json
import time
from typing import Optional, Dict, Any, List
from openai import OpenAI
from loguru import logger


PLATFORM_STYLES = {
    "zhihu": {
        "name": "知乎",
        "style": "专业深度，有理有据，适合知识分享，段落清晰，可使用小标题",
        "max_length": 5000,
        "format_hint": "使用 Markdown 格式，包含引言、正文（3-5个小节）、总结"
    },
    "wechat": {
        "name": "微信公众号",
        "style": "亲切易读，情感共鸣，适合大众阅读，段落短小，多用换行",
        "max_length": 3000,
        "format_hint": "开头吸引眼球，中间有料有趣，结尾引导互动"
    },
    "baijia": {
        "name": "百家号",
        "style": "资讯风格，标题党友好，内容实用，适合百度搜索流量",
        "max_length": 2000,
        "format_hint": "标题含关键词，内容分点列举，结尾有总结"
    },
    "toutiao": {
        "name": "今日头条",
        "style": "通俗易懂，贴近生活，有话题性，适合移动端阅读",
        "max_length": 2000,
        "format_hint": "短句为主，多用数字和列举，内容接地气"
    },
    "xiaohongshu": {
        "name": "小红书",
        "style": "种草风格，真实分享，多用emoji，标题含关键词",
        "max_length": 1000,
        "format_hint": "开头用emoji，分点分享，结尾加话题标签"
    },
    "general": {
        "name": "通用",
        "style": "清晰易读，结构完整",
        "max_length": 3000,
        "format_hint": "标准文章格式"
    }
}


class AICreator:
    """AI 内容创作器"""

    def __init__(self, api_key: Optional[str] = None, api_base: Optional[str] = None, model: str = "gpt-4.1-mini"):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.api_base = api_base  # None 表示使用环境变量中预配置的 base_url
        self.model = model
        self._client = None

    def _get_client(self) -> OpenAI:
        if self._client is None:
            kwargs = {"api_key": self.api_key}
            if self.api_base:
                kwargs["base_url"] = self.api_base
            self._client = OpenAI(**kwargs)
        return self._client

    def _chat(self, messages: List[Dict], temperature: float = 0.7, max_tokens: int = 3000) -> str:
        """调用 AI 接口"""
        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"AI 调用失败: {e}")
            raise

    def generate_titles(self, topic: str, platform: str = "general", count: int = 5) -> List[str]:
        """生成多个标题"""
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["general"])
        prompt = f"""请为以下主题生成 {count} 个吸引人的文章标题，适合{style['name']}平台风格（{style['style']}）。

主题：{topic}

要求：
1. 每个标题单独一行
2. 标题要有吸引力，能引发读者兴趣
3. 长度适中（15-30字）
4. 直接输出标题列表，不要编号，不要其他说明

请输出 {count} 个标题："""

        result = self._chat([{"role": "user", "content": prompt}], temperature=0.9)
        titles = [line.strip() for line in result.split('\n') if line.strip()]
        return titles[:count]

    def generate_outline(self, title: str, platform: str = "general") -> str:
        """生成文章大纲"""
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["general"])
        prompt = f"""请为以下文章标题生成详细大纲，适合{style['name']}平台（{style['style']}）。

标题：{title}
格式要求：{style['format_hint']}

请输出结构化大纲（包含主要章节和要点）："""

        return self._chat([{"role": "user", "content": prompt}], temperature=0.7)

    def generate_article(self, title: str, outline: Optional[str] = None,
                         platform: str = "general", keywords: Optional[List[str]] = None) -> Dict[str, str]:
        """生成完整文章"""
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["general"])
        kw_str = f"\n关键词（请自然融入）：{', '.join(keywords)}" if keywords else ""

        outline_str = f"\n参考大纲：\n{outline}" if outline else ""

        prompt = f"""请根据以下信息撰写一篇完整的文章，适合{style['name']}平台发布。

标题：{title}
平台风格：{style['style']}
格式要求：{style['format_hint']}
字数要求：{style['max_length']}字以内{kw_str}{outline_str}

请直接输出文章正文（不要重复标题）："""

        content = self._chat(
            [{"role": "user", "content": prompt}],
            temperature=0.75,
            max_tokens=min(style['max_length'] * 2, 4000)
        )

        # 生成摘要
        summary_prompt = f"请用50字以内概括以下文章的核心内容：\n\n{content[:500]}..."
        summary = self._chat([{"role": "user", "content": summary_prompt}], temperature=0.5, max_tokens=100)

        return {
            "title": title,
            "content": content,
            "summary": summary,
            "platform": platform,
            "word_count": len(content)
        }

    def rewrite_article(self, content: str, platform: str = "general",
                        style_hint: str = "") -> str:
        """改写文章（适配不同平台）"""
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["general"])
        hint_str = f"\n额外要求：{style_hint}" if style_hint else ""

        prompt = f"""请将以下文章改写为适合{style['name']}平台的风格。

改写要求：
- 平台风格：{style['style']}
- 格式要求：{style['format_hint']}
- 保持核心观点不变
- 字数控制在 {style['max_length']} 字以内{hint_str}

原文：
{content}

请输出改写后的文章："""

        return self._chat([{"role": "user", "content": prompt}], temperature=0.7, max_tokens=4000)

    def generate_tags(self, title: str, content: str, platform: str = "general") -> List[str]:
        """生成话题标签"""
        prompt = f"""请为以下文章生成5-8个适合{PLATFORM_STYLES.get(platform, PLATFORM_STYLES['general'])['name']}平台的话题标签。

标题：{title}
内容摘要：{content[:300]}

要求：
- 每行一个标签
- 不要加 # 符号
- 标签要精准相关

请输出标签列表："""

        result = self._chat([{"role": "user", "content": prompt}], temperature=0.6, max_tokens=200)
        tags = [line.strip().lstrip('#').strip() for line in result.split('\n') if line.strip()]
        return tags[:8]

    def fetch_hot_topics(self, category: str = "科技") -> List[Dict[str, str]]:
        """利用 AI 生成当前热门话题建议"""
        prompt = f"""请列出当前{category}领域最值得写作的10个热门话题或趋势，每个话题给出简短说明。

格式（每行一个）：
话题标题 | 简短说明

请输出10个话题："""

        result = self._chat([{"role": "user", "content": prompt}], temperature=0.8, max_tokens=1000)
        topics = []
        for line in result.split('\n'):
            if '|' in line:
                parts = line.split('|', 1)
                topics.append({
                    "title": parts[0].strip(),
                    "description": parts[1].strip() if len(parts) > 1 else ""
                })
        return topics[:10]

    def adapt_for_platform(self, title: str, content: str, target_platforms: List[str]) -> Dict[str, Dict]:
        """一键适配多个平台"""
        results = {}
        for platform in target_platforms:
            try:
                logger.info(f"正在适配平台：{platform}")
                adapted_content = self.rewrite_article(content, platform)
                adapted_tags = self.generate_tags(title, adapted_content, platform)
                results[platform] = {
                    "title": title,
                    "content": adapted_content,
                    "tags": adapted_tags,
                    "platform_name": PLATFORM_STYLES.get(platform, {}).get("name", platform)
                }
                time.sleep(0.5)  # 避免频繁调用
            except Exception as e:
                logger.error(f"适配平台 {platform} 失败: {e}")
                results[platform] = {"error": str(e)}
        return results
