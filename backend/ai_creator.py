"""
AI 内容创作模块
默认使用 DeepSeek V3（比 GPT-4o 便宜约 30 倍，中文效果极佳）
同时支持 Gemini Flash、Groq、通义千问等低成本模型
平台差异化：每个平台拥有独立的语气、结构、排版规则和专属格式模板
"""
import os
import json
import time
from typing import Optional, Dict, Any, List
from openai import OpenAI
from loguru import logger


# ===================== 模型提供商配置 =====================
# 所有提供商均兼容 OpenAI SDK，只需切换 base_url 和 api_key

MODEL_PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",          # DeepSeek-V3，$0.28/1M input，$0.42/1M output
        "models": {
            "deepseek-chat": "DeepSeek V3（推荐，性价比最高）",
            "deepseek-reasoner": "DeepSeek R1（推理增强版）",
        },
        "price_note": "输入 $0.28/1M tokens，输出 $0.42/1M tokens，比 GPT-4o 便宜约 30 倍"
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "env_key": "GEMINI_API_KEY",
        "default_model": "gemini-2.0-flash",       # $0.075/1M input，$0.30/1M output
        "models": {
            "gemini-2.0-flash": "Gemini 2.0 Flash（速度快，价格低）",
            "gemini-2.5-flash-preview-05-20": "Gemini 2.5 Flash（最新版）",
        },
        "price_note": "输入 $0.075/1M tokens，输出 $0.30/1M tokens"
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",  # 有免费额度，速度极快
        "models": {
            "llama-3.3-70b-versatile": "Llama 3.3 70B（免费额度大，速度极快）",
            "llama3-8b-8192": "Llama 3 8B（超快，适合简单任务）",
            "mixtral-8x7b-32768": "Mixtral 8x7B（长上下文）",
        },
        "price_note": "有免费额度，付费约 $0.59/1M tokens，速度极快"
    },
    "qwen": {
        "name": "阿里通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env_key": "DASHSCOPE_API_KEY",
        "default_model": "qwen-plus",              # 中文效果好，价格适中
        "models": {
            "qwen-plus": "通义千问 Plus（中文效果好）",
            "qwen-turbo": "通义千问 Turbo（速度快，价格低）",
            "qwen-max": "通义千问 Max（最强效果）",
        },
        "price_note": "qwen-turbo 约 ¥0.3/1M tokens，中文场景推荐"
    },
    "openai": {
        "name": "OpenAI",
        "base_url": None,                          # 使用默认
        "env_key": "OPENAI_API_KEY",
        "default_model": "gpt-4.1-mini",
        "models": {
            "gpt-4.1-mini": "GPT-4.1 Mini（平衡版）",
            "gpt-4o-mini": "GPT-4o Mini（经济版）",
        },
        "price_note": "gpt-4.1-mini 约 $0.40/1M tokens"
    }
}

# 默认优先级：DeepSeek > Gemini > Groq > 通义千问 > OpenAI
# 系统会按顺序检测哪个 API Key 已配置，自动选择
DEFAULT_PROVIDER_PRIORITY = ["deepseek", "gemini", "groq", "qwen", "openai"]


def detect_provider() -> Dict[str, str]:
    """自动检测已配置的 API Key，返回最优提供商信息"""
    for provider_key in DEFAULT_PROVIDER_PRIORITY:
        config = MODEL_PROVIDERS[provider_key]
        api_key = os.environ.get(config["env_key"], "")
        if api_key and api_key.strip():
            logger.info(f"✅ 检测到 {config['name']} API Key，将使用 {config['default_model']}")
            return {
                "provider": provider_key,
                "api_key": api_key.strip(),
                "base_url": config["base_url"],
                "model": config["default_model"],
                "name": config["name"]
            }
    # 兜底：使用环境变量中的通用配置（兼容旧版）
    fallback_key = os.environ.get("OPENAI_API_KEY", "")
    return {
        "provider": "openai",
        "api_key": fallback_key,
        "base_url": None,
        "model": "gpt-4.1-mini",
        "name": "OpenAI (fallback)"
    }


# ===================== 平台风格配置（深度差异化版） =====================

PLATFORM_STYLES = {
    "zhihu": {
        "name": "知乎",
        "icon": "🔵",
        "style": "专业、严谨、有深度，像一位行业专家在认真回答问题",
        "tone": "理性克制，用数据和逻辑说话，避免情绪化表达，适当引用研究或案例",
        "max_length": 5000,
        "min_length": 1500,
        "format_hint": "使用 Markdown 格式：开头用一段话点明核心观点，正文分 3-5 个带 ## 标题的章节，每节 300-500 字，结尾用「总结」收尾",
        "structure_template": """
## 引言（点明核心观点，50-100字）

## 一、[第一个论点]
[详细论述，引用数据/案例，300字左右]

## 二、[第二个论点]
[详细论述，300字左右]

## 三、[第三个论点或反驳常见误区]
[详细论述，300字左右]

## 总结
[100字以内，升华主题]
""",
        "forbidden": ["哈哈", "绝了", "yyds", "真的假的", "震惊", "！！！"],
        "special_rules": [
            "多用「我认为」「数据显示」「研究表明」等表达",
            "段落之间逻辑要有明显的递进或转折关系",
            "可以适当提出反问，引发读者思考",
            "专业术语要解释清楚，不要假设读者都懂"
        ]
    },
    "wechat": {
        "name": "微信公众号",
        "icon": "🟢",
        "style": "温暖、亲切、有共鸣感，像朋友在聊天，读者读完想转发",
        "tone": "口语化但不失质感，善用故事开头，情感共鸣是核心，结尾要有行动引导",
        "max_length": 3000,
        "min_length": 800,
        "format_hint": "段落短（每段2-4句），多空行，开头用故事或问题钩住读者，中间穿插金句，结尾引导关注/转发",
        "structure_template": """
[用一个小故事或场景开头，100字以内，制造共鸣]

[过渡句，引出主题]

**[小标题一]**

[正文，短段落，每段不超过4句话]

**[小标题二]**

[正文]

**[小标题三]**

[正文]

---

[金句总结，一句话点题]

[结尾：引导读者点赞/转发/留言，语气要自然不生硬]
""",
        "forbidden": ["综上所述", "本文将", "笔者认为", "由此可见", "总而言之"],
        "special_rules": [
            "开头前3句必须抓住读者，不能废话",
            "每隔3-4段加一个加粗的小标题",
            "多用「你」来称呼读者，增加代入感",
            "可以加入「我之前也以为……但其实……」这类反转句式",
            "结尾必须有互动引导，如「你有没有遇到过这种情况？」"
        ]
    },
    "xiaohongshu": {
        "name": "小红书",
        "icon": "🔴",
        "style": "真实种草、生活化、有画面感，像闺蜜在分享好东西",
        "tone": "活泼、热情、真实，大量使用 emoji，分点清晰，标题要有关键词",
        "max_length": 1000,
        "min_length": 300,
        "format_hint": "开头emoji+吸睛句，正文分3-6个要点（每点emoji开头），结尾加话题标签，全文不超过1000字",
        "structure_template": """
✨ [吸睛开头，说明这篇笔记能给读者带来什么价值]

📌 [要点一标题]
[2-3句具体描述，口语化，有细节]

📌 [要点二标题]
[2-3句具体描述]

📌 [要点三标题]
[2-3句具体描述]

💡 小tips：[一个实用小建议]

[结尾：真实感受或推荐语，1-2句]

#话题标签1 #话题标签2 #话题标签3 #话题标签4 #话题标签5
""",
        "forbidden": ["综上所述", "笔者", "本文", "研究表明", "数据显示"],
        "special_rules": [
            "每个段落开头必须用 emoji",
            "语气要像真实用户分享，不能像广告",
            "要有具体细节（比如价格、使用时长、具体效果）",
            "结尾必须有5个以上话题标签",
            "标题要包含核心关键词，方便搜索"
        ]
    },
    "toutiao": {
        "name": "今日头条",
        "icon": "🟠",
        "style": "通俗易懂、接地气、有话题性，适合移动端碎片化阅读",
        "tone": "直白、有冲击力，善用数字和对比，标题要有悬念或利益点",
        "max_length": 2500,
        "min_length": 600,
        "format_hint": "短句为主（每句不超过20字），多用数字列举，段落3-4句，小标题要有吸引力，结尾给出明确结论",
        "structure_template": """
[开头：用一个数字或反常识的结论吸引眼球，2-3句]

[背景交代，简短，不超过100字]

**[小标题一：用数字或疑问句]**

[正文，短句，每段3-4句，100字左右]

**[小标题二]**

[正文]

**[小标题三]**

[正文]

[结尾：给出明确结论或行动建议，50字以内]
""",
        "forbidden": ["笔者", "综上所述", "由此可见", "不难发现"],
        "special_rules": [
            "标题和小标题要有数字（如「3个方法」「90%的人不知道」）",
            "多用短句，避免长句",
            "每段开头直接说重点，不要铺垫",
            "可以用「你知道吗」「很多人以为」等引发好奇的句式",
            "结尾要有明确的观点或建议，不能虎头蛇尾"
        ]
    },
    "baijia": {
        "name": "百家号",
        "icon": "🔴",
        "style": "资讯风格，SEO 友好，内容实用，适合百度搜索流量",
        "tone": "客观、实用、信息量大，标题含核心关键词，内容有搜索价值",
        "max_length": 2500,
        "min_length": 800,
        "format_hint": "标题含关键词，开头直接说明文章价值，正文分点列举（用数字序号），每点有小标题，结尾总结并含关键词",
        "structure_template": """
[开头：直接说明本文解决什么问题，50字以内]

**一、[第一个要点，含关键词]**

[详细说明，150字左右，信息密度高]

**二、[第二个要点]**

[详细说明]

**三、[第三个要点]**

[详细说明]

**总结**

[100字以内，重申核心关键词，方便搜索收录]
""",
        "forbidden": ["哈哈", "yyds", "绝了", "震惊体"],
        "special_rules": [
            "标题和小标题要自然包含核心关键词",
            "内容要有实际可操作的信息，不能只讲概念",
            "多用「如何」「方法」「步骤」「技巧」等搜索友好词汇",
            "段落信息密度要高，避免废话",
            "结尾总结要重复核心关键词"
        ]
    },
    "weibo": {
        "name": "微博",
        "icon": "🟡",
        "style": "简短有力、有话题感、引发互动，像在广场上发言",
        "tone": "直接、有观点、带情绪（但不偏激），善用反问和感叹，引发评论",
        "max_length": 500,
        "min_length": 100,
        "format_hint": "正文不超过500字，开头直接亮观点，中间1-2个支撑点，结尾用问句引发互动，加3-5个话题标签",
        "structure_template": """
[直接亮出核心观点，1-2句，有力量感]

[补充说明或举例，2-3句]

[反问或引发思考的句子]

#话题一# #话题二# #话题三#
""",
        "forbidden": ["综上所述", "笔者认为", "本文将", "由此可见"],
        "special_rules": [
            "开头必须直接亮观点，不能铺垫",
            "全文不超过500字，精炼有力",
            "结尾用问句引发评论互动",
            "加话题标签，格式为 #话题名#",
            "语气可以稍微有点「刺」，引发讨论"
        ]
    },
    "bilibili": {
        "name": "B站专栏",
        "icon": "🔵",
        "style": "年轻化、有梗、专业与趣味并重，像UP主在认真讲干货",
        "tone": "活泼但有深度，可以用网络用语，但核心内容要扎实，适当自嘲和幽默",
        "max_length": 4000,
        "min_length": 1000,
        "format_hint": "开头用一个有趣的切入点，正文分章节（可以用「第一章」等），穿插有趣的比喻，结尾引导三连",
        "structure_template": """
[开头：用一个有趣的问题或反常识的现象切入，100字以内]

**第一部分：[标题]**

[正文，可以穿插有趣的比喻或梗，300字左右]

**第二部分：[标题]**

[正文，干货为主，可以适当幽默]

**第三部分：[标题]**

[正文]

**总结 & 彩蛋**

[总结核心内容，最后加一句引导三连的话，语气要真诚不油腻]
""",
        "forbidden": ["综上所述", "笔者", "由此可见"],
        "special_rules": [
            "可以适当使用「awsl」「破防」「绝绝子」等网络用语，但不要过度",
            "比喻要生动，让复杂概念变得易懂",
            "结尾引导三连要真诚，不要太功利",
            "内容要有「干货感」，读完有收获",
            "可以在关键处加「（划重点）」等提示"
        ]
    },
    "douyin": {
        "name": "抖音图文",
        "icon": "⚫",
        "style": "极简、冲击力强、3秒抓眼球，适合快速滑动浏览",
        "tone": "直接、有冲击力，每句话都要有价值，不废话，善用数字和对比",
        "max_length": 500,
        "min_length": 150,
        "format_hint": "每段只有1-2句话，大量空行，用数字列举，开头必须是最强的一句话，结尾有行动指引",
        "structure_template": """
[最强的一句话，直接说结论或最有冲击力的信息]

[补充说明，1句]

① [第一点，简短有力]

② [第二点]

③ [第三点]

[结尾：一句话行动指引，如「收藏备用」「评论告诉我」]
""",
        "forbidden": ["综上所述", "笔者", "本文", "由此可见", "不难发现"],
        "special_rules": [
            "开头第一句必须是全文最有冲击力的",
            "每段不超过2句话，大量使用换行",
            "多用数字（「3个」「90%」「第1步」）",
            "语言极度精炼，删掉所有废话",
            "结尾必须有明确的行动指引"
        ]
    },
    "general": {
        "name": "通用",
        "icon": "⚪",
        "style": "清晰易读，结构完整，适合多平台通用",
        "tone": "中性、专业，逻辑清晰",
        "max_length": 3000,
        "min_length": 500,
        "format_hint": "标准文章格式：引言 + 正文（3-4节）+ 结尾",
        "structure_template": "",
        "forbidden": [],
        "special_rules": []
    }
}


class AICreator:
    """AI 内容创作器（默认使用 DeepSeek，支持多模型切换）"""

    def __init__(self, api_key: Optional[str] = None, api_base: Optional[str] = None,
                 model: Optional[str] = None, provider: Optional[str] = None):
        # 如果没有显式指定，自动检测最优提供商
        if api_key:
            self.api_key = api_key
            self.api_base = api_base
            self.model = model or "deepseek-chat"
            self.provider = provider or "custom"
        else:
            detected = detect_provider()
            self.api_key = detected["api_key"]
            self.api_base = detected["base_url"]
            self.model = detected["model"]
            self.provider = detected["provider"]
            logger.info(f"🤖 使用模型：{detected['name']} / {self.model}")

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
            logger.error(f"AI 调用失败 [{self.provider}/{self.model}]: {e}")
            raise

    def _build_platform_system_prompt(self, platform: str) -> str:
        """构建平台专属系统提示词"""
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["general"])
        forbidden_str = "、".join(style.get("forbidden", [])) if style.get("forbidden") else "无"
        rules_str = "\n".join([f"- {r}" for r in style.get("special_rules", [])]) if style.get("special_rules") else "- 保持内容质量"

        return f"""你是一位专业的{style['name']}内容创作者，深度理解该平台的用户群体和内容规律。

【平台】{style['name']}
【核心风格】{style['style']}
【语气要求】{style['tone']}
【格式要求】{style['format_hint']}
【禁用表达】{forbidden_str}
【专属规则】
{rules_str}

请严格按照以上要求创作，确保内容在{style['name']}平台上有良好的阅读体验和传播效果。"""

    def generate_titles(self, topic: str, platform: str = "general", count: int = 5) -> List[str]:
        """生成多个标题"""
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["general"])

        platform_title_rules = {
            "zhihu": "标题要像一个深刻的问题或专业的观点，如「为什么XXX」「XXX的本质是什么」",
            "wechat": "标题要有情感共鸣或实用价值，如「那些XXX的人，后来都怎样了」「XXX，你一定要知道」",
            "xiaohongshu": "标题要含关键词，有种草感，如「XXX真的绝了！」「亲测有效的XXX方法」",
            "toutiao": "标题要有数字或悬念，如「3个方法让你XXX」「90%的人都不知道的XXX」",
            "baijia": "标题要含核心关键词，SEO友好，如「XXX方法大全」「如何XXX：完整指南」",
            "weibo": "标题要有话题感和争议性，引发转发，如「关于XXX，我想说实话」",
            "bilibili": "标题要有趣且有干货感，如「XXX，我研究了三个月终于搞明白了」",
            "douyin": "标题要极简有冲击力，如「XXX，看完你就懂了」「这个XXX技巧，99%的人不知道」",
            "general": "标题要清晰表达主题，有吸引力"
        }

        title_rule = platform_title_rules.get(platform, platform_title_rules["general"])
        system_prompt = self._build_platform_system_prompt(platform)

        user_prompt = f"""请为以下主题生成 {count} 个适合{style['name']}平台的文章标题。

主题：{topic}
标题风格要求：{title_rule}

要求：
1. 每个标题单独一行，不要编号
2. 标题长度 15-30 字
3. 每个标题风格要有所不同（疑问式、数字式、情感式等）
4. 直接输出标题，不要其他说明

请输出 {count} 个标题："""

        result = self._chat(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_prompt}],
            temperature=0.9
        )
        titles = [line.strip().lstrip('0123456789.-、 ').strip() for line in result.split('\n') if line.strip()]
        return titles[:count]

    def generate_outline(self, title: str, platform: str = "general") -> str:
        """生成文章大纲"""
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["general"])
        system_prompt = self._build_platform_system_prompt(platform)

        user_prompt = f"""请为以下文章标题生成详细大纲，严格符合{style['name']}平台的内容结构规范。

标题：{title}
字数范围：{style['min_length']}-{style['max_length']} 字
格式规范：{style['format_hint']}

请输出结构化大纲（包含主要章节、每节要点和预计字数）："""

        return self._chat(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_prompt}],
            temperature=0.7
        )

    def generate_article(self, title: str, outline: Optional[str] = None,
                         platform: str = "general", keywords: Optional[List[str]] = None,
                         user_requirement: Optional[str] = None) -> Dict[str, str]:
        """生成完整文章（深度差异化版，支持用户需求描述）"""
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["general"])
        kw_str = f"\n关键词（请自然融入正文）：{', '.join(keywords)}" if keywords else ""
        outline_str = f"\n参考大纲：\n{outline}" if outline else ""
        template_str = f"\n推荐结构模板（可参考）：\n{style['structure_template']}" if style.get("structure_template") else ""
        # 用户需求：最高优先级，直接告诉 AI 要传达什么
        req_str = f"\n\n【核心传达目标 - 最高优先级】\n用户希望通过这篇文章让读者了解/感受到：\n{user_requirement}\n请确保文章的核心内容、论点和情感都围绕这个目标展开。" if user_requirement else ""

        system_prompt = self._build_platform_system_prompt(platform)

        user_prompt = f"""请根据以下信息，撰写一篇完整的{style['name']}平台文章。

标题：{title}
目标字数：{style['min_length']}-{style['max_length']} 字{kw_str}{outline_str}{template_str}{req_str}

重要提醒：
- 这是专门为【{style['name']}】平台创作的内容，请完全按照该平台的风格、语气和格式规范来写
- 语气：{style['tone']}
- 禁止使用：{', '.join(style.get('forbidden', [])) if style.get('forbidden') else '无特殊限制'}

请直接输出文章正文（不要重复标题）："""

        content = self._chat(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_prompt}],
            temperature=0.78,
            max_tokens=min(style['max_length'] * 2, 4000)
        )

        # 生成摘要
        summary_prompt = f"请用50字以内概括以下文章的核心内容：\n\n{content[:500]}..."
        summary = self._chat(
            [{"role": "user", "content": summary_prompt}],
            temperature=0.5, max_tokens=100
        )

        return {
            "title": title,
            "content": content,
            "summary": summary,
            "platform": platform,
            "platform_name": style["name"],
            "word_count": len(content),
            "model_used": f"{self.provider}/{self.model}"
        }

    def rewrite_article(self, content: str, platform: str = "general",
                        style_hint: str = "") -> str:
        """改写文章（深度适配目标平台风格）"""
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["general"])
        hint_str = f"\n额外要求：{style_hint}" if style_hint else ""
        system_prompt = self._build_platform_system_prompt(platform)

        user_prompt = f"""请将以下文章彻底改写为适合【{style['name']}】平台的版本。

改写要求：
- 保持核心观点和信息不变
- 完全重构语气、句式、段落结构，使其符合{style['name']}平台的内容规范
- 目标字数：{style['min_length']}-{style['max_length']} 字
- 语气：{style['tone']}
- 格式：{style['format_hint']}{hint_str}

原文：
{content}

请输出改写后的完整文章（不要输出原文，直接给改写版本）："""

        return self._chat(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_prompt}],
            temperature=0.75, max_tokens=4000
        )

    def generate_tags(self, title: str, content: str, platform: str = "general") -> List[str]:
        """生成话题标签"""
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["general"])

        tag_format_rules = {
            "xiaohongshu": "标签要适合小红书搜索，包含品类词、场景词、人群词",
            "weibo": "标签要有话题感，适合微博热搜",
            "toutiao": "标签要是常见搜索词，SEO友好",
            "zhihu": "标签要是知乎话题分类词，专业准确",
            "general": "标签要精准相关"
        }

        tag_rule = tag_format_rules.get(platform, tag_format_rules["general"])

        prompt = f"""请为以下{style['name']}平台的文章生成 5-8 个话题标签。

标题：{title}
内容摘要：{content[:300]}
标签规则：{tag_rule}

要求：每行一个标签，不要加 # 符号，标签要精准相关

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
        """一键适配多个平台（每个平台真正差异化改写）"""
        results = {}
        for platform in target_platforms:
            try:
                logger.info(f"正在为【{PLATFORM_STYLES.get(platform, {}).get('name', platform)}】深度适配内容...")
                adapted_content = self.rewrite_article(content, platform)
                adapted_tags = self.generate_tags(title, adapted_content, platform)
                style = PLATFORM_STYLES.get(platform, {})
                results[platform] = {
                    "title": self._adapt_title(title, platform),
                    "content": adapted_content,
                    "tags": adapted_tags,
                    "platform_name": style.get("name", platform),
                    "platform_icon": style.get("icon", "⚪"),
                    "word_count": len(adapted_content),
                    "style_summary": style.get("style", "")
                }
                time.sleep(0.3)
            except Exception as e:
                logger.error(f"适配平台 {platform} 失败: {e}")
                results[platform] = {"error": str(e)}
        return results

    def _adapt_title(self, original_title: str, platform: str) -> str:
        """为特定平台优化标题"""
        style = PLATFORM_STYLES.get(platform, PLATFORM_STYLES["general"])

        platform_title_hints = {
            "zhihu": "改写为知乎风格：深度提问或专业观点式标题",
            "wechat": "改写为公众号风格：情感共鸣或实用价值式标题",
            "xiaohongshu": "改写为小红书风格：种草感强、含关键词的标题",
            "toutiao": "改写为头条风格：含数字或悬念的标题",
            "baijia": "改写为百家号风格：含关键词、SEO友好的标题",
            "weibo": "改写为微博风格：简短有力、有话题感的标题",
            "bilibili": "改写为B站风格：有趣且有干货感的标题",
            "douyin": "改写为抖音风格：极简冲击力强的标题",
        }

        hint = platform_title_hints.get(platform, "保持原标题")
        if hint == "保持原标题":
            return original_title

        prompt = f"""请将以下标题改写为适合{style['name']}平台的版本。

原标题：{original_title}
改写要求：{hint}
长度：15-30字

只输出改写后的标题，不要其他内容："""

        try:
            return self._chat([{"role": "user", "content": prompt}], temperature=0.8, max_tokens=50)
        except Exception:
            return original_title

    def get_current_model_info(self) -> Dict[str, str]:
        """获取当前使用的模型信息"""
        provider_config = MODEL_PROVIDERS.get(self.provider, {})
        return {
            "provider": self.provider,
            "provider_name": provider_config.get("name", self.provider),
            "model": self.model,
            "price_note": provider_config.get("price_note", ""),
        }
