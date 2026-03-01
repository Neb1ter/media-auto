"""
内容审核模块（移植并增强自 web3pro）
三层审核流水线：
  Layer 1 — 本地敏感词检测（数据库词库，毫秒级）
  Layer 2 — Qwen 语义审核（通义千问 + 阿里云 CIP 内容安全，秒级）
  Layer 3 — AI 合规改写（DeepSeek/Qwen 自动修复违规内容）

设计原则：
  - 每层可独立启用/禁用
  - 未配置 DASHSCOPE_API_KEY 时，Layer 2/3 优雅降级（返回人工审核提示）
  - 所有审核结果结构化输出，方便前端展示
"""

import os
import re
import json
import sqlite3
import time
from typing import Optional, List, Dict, Any
from pathlib import Path
from openai import OpenAI
from loguru import logger

BASE_DIR = Path(__file__).parent.parent
# 优先使用环境变量 DATA_DIR（Railway Volume 挂载点）
_DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "db")))
_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = _DATA_DIR / "media_auto.db"

# ─── 类型定义 ──────────────────────────────────────────────────────────────────

class SensitiveWordMatch:
    def __init__(self, word: str, severity: str, replacement: Optional[str],
                 platforms: str, positions: List[int]):
        self.word = word
        self.severity = severity          # block | warn | replace
        self.replacement = replacement
        self.platforms = platforms
        self.positions = positions

    def to_dict(self) -> Dict:
        return {
            "word": self.word,
            "severity": self.severity,
            "replacement": self.replacement,
            "platforms": self.platforms,
            "positions": self.positions,
        }


class ModerationResult:
    """完整的三层审核结果"""

    def __init__(self):
        # Layer 1 结果
        self.sensitive_words: List[SensitiveWordMatch] = []
        self.has_blocked_words: bool = False

        # Layer 2 结果（Qwen）
        self.qwen_passed: bool = True
        self.qwen_score: int = 100
        self.qwen_issues: List[Dict] = []
        self.qwen_platform_suggestions: Dict[str, bool] = {}
        self.qwen_summary: str = ""
        self.qwen_skipped: bool = False   # True = 未配置 Key，跳过

        # Layer 3 结果
        self.rewritten_content: Optional[str] = None
        self.was_rewritten: bool = False

        # 最终结论
        self.final_passed: bool = True
        self.final_content: str = ""
        self.requires_manual_review: bool = False

    def to_dict(self) -> Dict:
        return {
            "layer1": {
                "sensitive_words": [w.to_dict() for w in self.sensitive_words],
                "has_blocked_words": self.has_blocked_words,
                "word_count": len(self.sensitive_words),
            },
            "layer2": {
                "passed": self.qwen_passed,
                "score": self.qwen_score,
                "issues": self.qwen_issues,
                "platform_suggestions": self.qwen_platform_suggestions,
                "summary": self.qwen_summary,
                "skipped": self.qwen_skipped,
            },
            "layer3": {
                "was_rewritten": self.was_rewritten,
                "rewritten": self.rewritten_content is not None,
            },
            "final": {
                "passed": self.final_passed,
                "requires_manual_review": self.requires_manual_review,
                "content": self.final_content,
            }
        }


# ─── 内置敏感词库（无数据库时的兜底词库） ─────────────────────────────────────

BUILTIN_SENSITIVE_WORDS = [
    # 金融违规
    {"word": "稳赚不赔", "severity": "block", "platforms": "all", "replacement": None},
    {"word": "保证盈利", "severity": "block", "platforms": "all", "replacement": None},
    {"word": "内幕消息", "severity": "block", "platforms": "all", "replacement": None},
    {"word": "稳定收益", "severity": "warn", "platforms": "wechat,weibo,douyin", "replacement": "潜在收益"},
    {"word": "零风险", "severity": "block", "platforms": "all", "replacement": None},
    {"word": "百分之百", "severity": "warn", "platforms": "wechat,weibo", "replacement": "较高概率"},
    {"word": "躺赚", "severity": "warn", "platforms": "all", "replacement": "被动收益"},
    {"word": "暴富", "severity": "block", "platforms": "all", "replacement": None},
    {"word": "一夜暴富", "severity": "block", "platforms": "all", "replacement": None},
    {"word": "割韭菜", "severity": "warn", "platforms": "wechat,weibo,douyin", "replacement": "市场风险"},
    {"word": "庄家", "severity": "warn", "platforms": "all", "replacement": "主力资金"},
    {"word": "内幕", "severity": "warn", "platforms": "all", "replacement": "市场信息"},
    # 营销违规
    {"word": "私信我", "severity": "warn", "platforms": "wechat,weibo", "replacement": "欢迎联系"},
    {"word": "加微信", "severity": "warn", "platforms": "weibo,douyin", "replacement": "欢迎交流"},
    {"word": "限时秒杀", "severity": "warn", "platforms": "wechat,weibo,douyin", "replacement": "限时优惠"},
    {"word": "免费领取", "severity": "warn", "platforms": "wechat,weibo", "replacement": "限时获取"},
    {"word": "转发抽奖", "severity": "block", "platforms": "wechat", "replacement": None},
    # 夸大宣传
    {"word": "震惊全球", "severity": "warn", "platforms": "all", "replacement": "引发关注"},
    {"word": "史上最强", "severity": "warn", "platforms": "all", "replacement": "极具竞争力"},
    {"word": "绝对安全", "severity": "block", "platforms": "all", "replacement": None},
    # 平台专属禁词
    {"word": "点击关注", "severity": "warn", "platforms": "wechat", "replacement": "欢迎关注"},
    {"word": "在看", "severity": "warn", "platforms": "weibo,douyin", "replacement": None},
]


# ─── Layer 1：本地敏感词检测 ───────────────────────────────────────────────────

def _load_words_from_db(target_platforms: List[str]) -> List[Dict]:
    """从 SQLite 数据库加载敏感词"""
    if not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT word, severity, replacement, platforms FROM sensitive_words WHERE is_active = 1"
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {"word": r[0], "severity": r[1], "replacement": r[2], "platforms": r[3]}
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"加载数据库敏感词失败，使用内置词库: {e}")
        return []


def check_sensitive_words(text: str, target_platforms: List[str] = None) -> List[SensitiveWordMatch]:
    """
    Layer 1：本地敏感词检测
    优先使用数据库词库，无数据库时使用内置词库
    """
    if target_platforms is None:
        target_platforms = ["all"]

    # 加载词库
    db_words = _load_words_from_db(target_platforms)
    word_list = db_words if db_words else BUILTIN_SENSITIVE_WORDS

    matches = []
    lower_text = text.lower()

    for entry in word_list:
        word = entry["word"]
        platforms = entry.get("platforms", "all")

        # 检查是否适用于目标平台
        word_platforms = [p.strip() for p in platforms.split(",")]
        applies = "all" in word_platforms or any(tp in word_platforms for tp in target_platforms)
        if not applies:
            continue

        # 查找所有出现位置
        positions = []
        lower_word = word.lower()
        idx = 0
        while True:
            pos = lower_text.find(lower_word, idx)
            if pos == -1:
                break
            positions.append(pos)
            idx = pos + 1

        if positions:
            matches.append(SensitiveWordMatch(
                word=word,
                severity=entry.get("severity", "warn"),
                replacement=entry.get("replacement"),
                platforms=platforms,
                positions=positions,
            ))

    return matches


def auto_replace_sensitive_words(text: str, matches: List[SensitiveWordMatch]) -> str:
    """自动替换 severity=replace 且有 replacement 的敏感词"""
    result = text
    for match in matches:
        if match.severity == "replace" and match.replacement:
            result = re.sub(re.escape(match.word), match.replacement, result, flags=re.IGNORECASE)
    return result


def clean_markdown(text: str) -> str:
    """
    清理 AI 生成内容中的排版问题（移植自 web3pro cleanMarkdown）
    1. 删除空的加粗/斜体标记（如 "**  **"）
    2. 删除行首行尾独立的星号
    3. 修复四个以上连续星号
    4. 压缩超过2个连续空行
    5. 删除重复的中文标点
    6. 删除行末多余空格
    """
    text = re.sub(r'\*{1,3}\s*\*{1,3}', '', text)
    text = re.sub(r'^\*{2,}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*\*{2,}$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*{4,}', '**', text)
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'([。，！？；：、]){2,}', r'\1', text)
    return text.strip()


# ─── Layer 2：Qwen 语义审核 ────────────────────────────────────────────────────

PLATFORM_NAMES = {
    "wechat": "微信公众号",
    "weibo": "微博",
    "xiaohongshu": "小红书",
    "toutiao": "今日头条",
    "baijia": "百家号",
    "bilibili": "B站专栏",
    "douyin": "抖音图文",
    "zhihu": "知乎",
    "general": "通用平台",
}

QWEN_SYSTEM_PROMPT = """你是一位专业的中国互联网内容合规审核专家，熟悉微信公众号、微博、抖音、小红书、知乎等平台的内容规范，以及中国相关法律法规对自媒体内容的要求。

请对用户提交的文章进行全面合规审核，重点检查：
1. 政治敏感内容（涉及党政领导人、政治事件、境外势力等）
2. 金融违规（虚假收益承诺、诱导投资、内幕消息、保证盈利等）
3. 违禁词汇（各平台敏感词、违禁词）
4. 不实信息（夸大事实、虚假数据、标题党）
5. 违规营销（诱导转发、虚假福利、诱导关注等）
6. 版权风险（大段引用未注明来源等）

请严格按照以下 JSON 格式返回审核结果，不要添加任何其他内容：
{
  "passed": true或false,
  "score": 0到100的整数（100为完全合规），
  "issues": [
    {
      "type": "politics/finance/vulgar/spam/copyright/other",
      "severity": "low/medium/high",
      "description": "具体问题描述（指出原文中的问题位置）",
      "suggestion": "具体修改建议"
    }
  ],
  "platformSuggestions": {
    "wechat": true或false,
    "weibo": true或false,
    "xiaohongshu": true或false,
    "toutiao": true或false,
    "douyin": true或false,
    "zhihu": true或false
  },
  "summary": "总体审核结论（50字以内）"
}"""


def moderate_with_qwen(
    title: str,
    content: str,
    platforms: List[str] = None
) -> Dict[str, Any]:
    """
    Layer 2：Qwen 语义审核
    使用通义千问 + 阿里云内容安全 CIP 进行深度语义审核
    未配置 DASHSCOPE_API_KEY 时优雅降级
    """
    if platforms is None:
        platforms = ["wechat", "weibo", "xiaohongshu", "toutiao", "douyin", "zhihu"]

    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()

    # 未配置 Key，优雅降级
    if not api_key:
        logger.info("未配置 DASHSCOPE_API_KEY，跳过 Qwen 语义审核")
        return {
            "passed": True,
            "score": 80,
            "issues": [],
            "platform_suggestions": {p: True for p in platforms},
            "summary": "未配置通义千问 API Key，跳过 AI 语义审核，建议人工复核",
            "skipped": True,
        }

    platform_list = "、".join([PLATFORM_NAMES.get(p, p) for p in platforms])
    text_to_check = f"标题：{title}\n\n正文：{content[:3000]}"

    try:
        client = OpenAI(
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

        response = client.chat.completions.create(
            model="qwen-plus",
            messages=[
                {"role": "system", "content": QWEN_SYSTEM_PROMPT},
                {"role": "user", "content": f"请审核以下文章（目标发布平台：{platform_list}）：\n\n{text_to_check}"}
            ],
            temperature=0.1,
            max_tokens=1500
        )

        raw = response.choices[0].message.content or ""
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if not json_match:
            raise ValueError("无法解析 Qwen 审核结果")

        result = json.loads(json_match.group())

        # 有 high 级别问题则强制不通过
        has_high = any(i.get("severity") == "high" for i in result.get("issues", []))
        if has_high:
            result["passed"] = False

        # 标准化字段名（camelCase → snake_case）
        return {
            "passed": result.get("passed", True),
            "score": result.get("score", 85),
            "issues": result.get("issues", []),
            "platform_suggestions": result.get("platformSuggestions", {p: True for p in platforms}),
            "summary": result.get("summary", "审核完成"),
            "skipped": False,
        }

    except Exception as e:
        err_str = str(e)
        # 阿里云 CIP 拦截（400 data_inspection_failed）
        if "data_inspection_failed" in err_str:
            logger.warning("阿里云 CIP 内容安全检测到违规内容")
            return {
                "passed": False,
                "score": 0,
                "issues": [{
                    "type": "politics",
                    "severity": "high",
                    "description": "阿里云内容安全服务检测到违规内容",
                    "suggestion": "请删除或修改违规内容后重新提交"
                }],
                "platform_suggestions": {p: False for p in platforms},
                "summary": "内容安全检测不通过，存在违规内容",
                "skipped": False,
            }

        logger.error(f"Qwen 审核失败: {e}")
        return {
            "passed": False,
            "score": 50,
            "issues": [{
                "type": "other",
                "severity": "low",
                "description": f"AI 审核服务暂时不可用: {err_str[:100]}",
                "suggestion": "请人工审核后决定是否发布"
            }],
            "platform_suggestions": {p: False for p in platforms},
            "summary": "AI 审核服务暂时不可用，请人工审核",
            "skipped": False,
        }


# ─── Layer 3：AI 合规改写 ──────────────────────────────────────────────────────

def rewrite_for_compliance(
    content: str,
    sensitive_matches: List[SensitiveWordMatch],
    qwen_issues: List[Dict],
    provider_client: Optional[OpenAI] = None,
    model: str = "deepseek-chat"
) -> str:
    """
    Layer 3：AI 合规改写
    综合 Layer 1 的敏感词和 Layer 2 的语义问题，一次性改写修复
    """
    # 收集所有需要修复的问题
    word_issues = [
        f'"{m.word}"'
        + (f'（建议替换为"{m.replacement}"）' if m.replacement else "（需删除或改写）")
        for m in sensitive_matches
        if m.severity in ("block", "warn")
    ]

    semantic_issues = [
        f'{i.get("description", "")}（建议：{i.get("suggestion", "")}）'
        for i in qwen_issues
        if i.get("severity") in ("medium", "high")
    ]

    all_issues = word_issues + semantic_issues
    if not all_issues:
        return content

    issues_text = "\n".join([f"{idx+1}. {issue}" for idx, issue in enumerate(all_issues)])

    prompt = f"""请对以下文章内容进行合规改写，要求：
1. 修复以下问题：
{issues_text}

2. 保持文章原意、结构和风格不变
3. 改写后的内容需符合中国主流媒体平台规范
4. 只返回改写后的文章内容，不要添加任何说明或前缀

原文：
{content}"""

    try:
        if provider_client is None:
            # 自动选择可用的客户端
            from ai_creator import detect_provider
            detected = detect_provider()
            kwargs = {"api_key": detected["api_key"]}
            if detected["base_url"]:
                kwargs["base_url"] = detected["base_url"]
            provider_client = OpenAI(**kwargs)
            model = detected["model"]

        response = provider_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=4000,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"合规改写失败: {e}")
        return content  # 改写失败时返回原文


# ─── 主审核流水线 ──────────────────────────────────────────────────────────────

def run_moderation_pipeline(
    title: str,
    content: str,
    platforms: List[str] = None,
    auto_rewrite: bool = True,
    provider_client: Optional[OpenAI] = None,
    model: str = "deepseek-chat"
) -> ModerationResult:
    """
    运行完整的三层审核流水线

    Args:
        title:           文章标题
        content:         文章正文
        platforms:       目标发布平台列表
        auto_rewrite:    是否自动进行合规改写（Layer 3）
        provider_client: AI 客户端（用于 Layer 3 改写）
        model:           改写使用的模型

    Returns:
        ModerationResult 包含三层审核的完整结果
    """
    if platforms is None:
        platforms = ["wechat", "weibo", "xiaohongshu", "toutiao", "douyin", "zhihu"]

    result = ModerationResult()
    full_text = f"{title}\n\n{content}"

    logger.info(f"🔍 开始内容审核 | 标题: {title[:30]}... | 平台: {', '.join(platforms)}")

    # ── Layer 1：本地敏感词检测 ────────────────────────────────────────────────
    logger.info("Layer 1: 本地敏感词检测...")
    result.sensitive_words = check_sensitive_words(full_text, platforms)
    result.has_blocked_words = any(w.severity == "block" for w in result.sensitive_words)

    blocked = [w.word for w in result.sensitive_words if w.severity == "block"]
    warned = [w.word for w in result.sensitive_words if w.severity == "warn"]
    if blocked:
        logger.warning(f"Layer 1: 发现 {len(blocked)} 个 block 级敏感词: {blocked}")
    if warned:
        logger.info(f"Layer 1: 发现 {len(warned)} 个 warn 级敏感词: {warned}")

    # ── Layer 2：Qwen 语义审核 ─────────────────────────────────────────────────
    logger.info("Layer 2: Qwen 语义审核...")
    qwen_result = moderate_with_qwen(title, content, platforms)
    result.qwen_passed = qwen_result["passed"]
    result.qwen_score = qwen_result["score"]
    result.qwen_issues = qwen_result["issues"]
    result.qwen_platform_suggestions = qwen_result["platform_suggestions"]
    result.qwen_summary = qwen_result["summary"]
    result.qwen_skipped = qwen_result.get("skipped", False)
    logger.info(f"Layer 2: 评分={result.qwen_score}, 通过={result.qwen_passed}, 问题数={len(result.qwen_issues)}")

    # ── 判断是否需要改写 ───────────────────────────────────────────────────────
    needs_rewrite = (
        result.has_blocked_words
        or any(w.severity in ("block", "warn") for w in result.sensitive_words)
        or any(i.get("severity") in ("medium", "high") for i in result.qwen_issues)
    )

    # ── Layer 3：AI 合规改写 ───────────────────────────────────────────────────
    if auto_rewrite and needs_rewrite:
        logger.info("Layer 3: AI 合规改写...")
        rewritten = rewrite_for_compliance(
            content,
            result.sensitive_words,
            result.qwen_issues,
            provider_client,
            model
        )
        if rewritten != content:
            result.rewritten_content = clean_markdown(rewritten)
            result.was_rewritten = True
            logger.info("Layer 3: 合规改写完成")
    elif not needs_rewrite:
        logger.info("Layer 3: 内容无需改写")

    # ── 最终结论 ───────────────────────────────────────────────────────────────
    result.final_content = result.rewritten_content if result.was_rewritten else clean_markdown(content)

    # 通过条件：Layer 1 无 block 词 + Layer 2 通过（或跳过）
    result.final_passed = (
        not result.has_blocked_words
        and (result.qwen_passed or result.qwen_skipped)
    )

    # 需要人工审核：有 warn 级问题但已改写，或 Qwen 跳过
    result.requires_manual_review = (
        result.qwen_skipped
        or (not result.final_passed and result.was_rewritten)
        or any(i.get("severity") == "low" for i in result.qwen_issues)
    )

    status_icon = "✅" if result.final_passed else "❌"
    logger.info(f"{status_icon} 审核完成 | 最终通过={result.final_passed} | 评分={result.qwen_score} | 已改写={result.was_rewritten}")

    return result


# ─── 便捷函数（供 main.py 直接调用） ──────────────────────────────────────────

def moderate_content(
    title: str,
    content: str,
    platforms: List[str] = None,
    auto_rewrite: bool = True
) -> Dict[str, Any]:
    """
    便捷审核函数，返回可直接序列化的字典
    供 FastAPI 接口直接调用
    """
    result = run_moderation_pipeline(
        title=title,
        content=content,
        platforms=platforms,
        auto_rewrite=auto_rewrite,
    )
    return result.to_dict()
