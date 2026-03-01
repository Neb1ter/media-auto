"""
自然语言查询引擎（NL → SQL → 结构化结果）
基于 DeepSeek / 任意 OpenAI 兼容模型，将用户的自然语言问题转换为 SQL 查询，
执行后返回结构化数据和可视化建议，供前端渲染图表。

数据库表结构（SQLite media_ops.db）：
  - articles       : 文章草稿与发布记录
  - publish_tasks  : 各平台发布任务
  - platform_accounts : 平台账号
  - ai_configs     : AI 模型配置
  - hot_topics     : 热点话题
  - system_logs    : 系统日志
"""

import os
import re
import json
import sqlite3
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from loguru import logger

# ─── 数据库路径（与 models.py 保持一致）─────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
_DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "db")))
DB_PATH = _DATA_DIR / "media_ops.db"

# ─── 数据库 Schema 描述（注入 Prompt，帮助 LLM 理解表结构）────────────────────
DB_SCHEMA = """
数据库：SQLite，文件路径 media_ops.db
包含以下表：

1. articles（文章表）
   - id INTEGER PRIMARY KEY
   - title VARCHAR(500)           文章标题
   - content TEXT                 文章正文（Markdown）
   - summary TEXT                 摘要
   - tags VARCHAR(500)            标签，逗号分隔
   - category VARCHAR(100)        分类：analysis/tutorial/news_decode/project/promo/report
   - status VARCHAR(50)           状态：draft/published/scheduled
   - created_at DATETIME          创建时间
   - updated_at DATETIME          更新时间
   - ai_generated BOOLEAN         是否 AI 生成（1=AI生成，0=手动）
   - word_count INTEGER           字数

2. publish_tasks（发布任务表）
   - id INTEGER PRIMARY KEY
   - article_id INTEGER           关联文章 ID
   - platform VARCHAR(100)        平台：zhihu/wechat/baijia/toutiao/xiaohongshu/weibo/bilibili/douyin
   - account_name VARCHAR(200)    账号名称
   - status VARCHAR(50)           状态：pending/running/success/failed
   - scheduled_at DATETIME        计划发布时间（可为 NULL）
   - published_at DATETIME        实际发布时间（可为 NULL）
   - error_msg TEXT               失败原因（可为 NULL）
   - created_at DATETIME          任务创建时间
   - result_url VARCHAR(500)      发布后的文章 URL（可为 NULL）

3. platform_accounts（平台账号表）
   - id INTEGER PRIMARY KEY
   - platform VARCHAR(100)        平台名称
   - account_name VARCHAR(200)    账号名称
   - status VARCHAR(50)           状态：active/expired/disabled
   - last_check DATETIME          最后检查时间
   - created_at DATETIME          创建时间

4. hot_topics（热点话题表）
   - id INTEGER PRIMARY KEY
   - title VARCHAR(500)           话题标题
   - source VARCHAR(100)          来源
   - heat_score FLOAT             热度分数
   - url VARCHAR(500)             原文链接
   - fetched_at DATETIME          抓取时间
   - used BOOLEAN                 是否已使用

5. system_logs（系统日志表）
   - id INTEGER PRIMARY KEY
   - level VARCHAR(50)            日志级别：INFO/WARNING/ERROR
   - module VARCHAR(100)          模块名称
   - message TEXT                 日志内容
   - created_at DATETIME          记录时间
"""

# ─── 可视化类型建议规则 ──────────────────────────────────────────────────────
CHART_RULES = """
根据查询结果的数据特征，建议以下可视化类型：
- 单个数值（如总数、成功率）→ stat_card（数字卡片）
- 两列数据（类别 + 数值）且类别 ≤ 8 → bar（柱状图）
- 两列数据（类别 + 数值）且类别 > 8 → table（表格）
- 时间序列（日期 + 数值）→ line（折线图）
- 占比数据（类别 + 百分比）且类别 ≤ 6 → pie（饼图）
- 多列数据 → table（表格）
- 对比两组数据 → bar_grouped（分组柱状图）
"""


class NLQueryEngine:
    """自然语言查询引擎"""

    def __init__(self, api_key: Optional[str] = None, api_base: Optional[str] = None,
                 model: Optional[str] = None):
        # 复用 ai_creator 的模型检测逻辑
        from ai_creator import detect_provider
        if api_key:
            self.api_key = api_key
            self.api_base = api_base
            self.model = model or "deepseek-chat"
        else:
            detected = detect_provider()
            self.api_key = detected["api_key"]
            self.api_base = detected["base_url"]
            self.model = detected["model"]
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            kwargs = {"api_key": self.api_key}
            if self.api_base:
                kwargs["base_url"] = self.api_base
            self._client = OpenAI(**kwargs)
        return self._client

    def _nl_to_sql(self, question: str) -> Dict[str, Any]:
        """
        第一步：将自然语言问题转换为 SQL 查询
        返回：{ sql, explanation, chart_type, x_field, y_field, title }
        """
        system_prompt = f"""你是一个专业的数据分析助手，能将自然语言问题转换为精确的 SQLite SQL 查询。

{DB_SCHEMA}

{CHART_RULES}

重要规则：
1. 只能使用 SELECT 语句，禁止 INSERT/UPDATE/DELETE/DROP 等修改操作
2. 时间相关查询使用 SQLite 的 datetime() 函数，如 datetime('now', '-30 days')
3. 布尔值在 SQLite 中存储为 0/1，ai_generated=1 表示 AI 生成
4. 平台名称使用英文小写：zhihu/wechat/baijia/toutiao/xiaohongshu/weibo/bilibili/douyin
5. 状态值使用英文：success/failed/pending/running/draft/published
6. 如果问题无法用现有表回答，返回 sql 为空字符串
7. 字段别名使用中文，方便展示（如 COUNT(*) AS 发布次数）
8. 成功率计算：ROUND(SUM(CASE WHEN status='success' THEN 1.0 ELSE 0 END) / COUNT(*) * 100, 1)

请输出 JSON 格式（不要有任何其他文字）：
{{
  "sql": "SELECT ...",
  "explanation": "这个查询的含义：...",
  "chart_type": "bar|line|pie|table|stat_card|bar_grouped",
  "chart_title": "图表标题",
  "x_field": "X轴字段名（类别字段）",
  "y_field": "Y轴字段名（数值字段）",
  "insight": "数据洞察提示（告诉用户如何解读这个数据）"
}}"""

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"问题：{question}"}
                ],
                temperature=0.1,
                max_tokens=1000,
            )
            raw = response.choices[0].message.content.strip()
            # 提取 JSON
            json_match = re.search(r'\{[\s\S]*\}', raw)
            if json_match:
                return json.loads(json_match.group())
            return {"sql": "", "explanation": "无法解析 AI 返回内容", "chart_type": "table"}
        except Exception as e:
            logger.error(f"NL→SQL 转换失败: {e}")
            raise

    def _execute_sql(self, sql: str) -> Tuple[List[str], List[List]]:
        """
        第二步：执行 SQL，返回 (columns, rows)
        """
        if not DB_PATH.exists():
            # 数据库不存在时返回空结果（演示模式）
            return [], []

        # 安全检查：只允许 SELECT
        sql_clean = sql.strip().upper()
        if not sql_clean.startswith("SELECT"):
            raise ValueError("只允许执行 SELECT 查询")
        # 禁止危险关键字
        dangerous = ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "TRUNCATE"]
        for kw in dangerous:
            if kw in sql_clean:
                raise ValueError(f"查询包含禁止的关键字: {kw}")

        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            if not rows:
                conn.close()
                return [], []
            columns = list(rows[0].keys())
            data = [list(row) for row in rows]
            conn.close()
            return columns, data
        except sqlite3.Error as e:
            logger.error(f"SQL 执行失败: {e}\nSQL: {sql}")
            raise ValueError(f"SQL 执行错误: {str(e)}")

    def _generate_mock_data(self, question: str, sql_meta: Dict) -> Tuple[List[str], List[List]]:
        """
        当数据库为空或不存在时，生成演示数据
        """
        chart_type = sql_meta.get("chart_type", "table")
        q_lower = question.lower()

        if "成功率" in question or "success" in q_lower:
            return (
                ["平台", "发布次数", "成功率(%)"],
                [
                    ["知乎", 45, 95.6],
                    ["微信公众号", 38, 92.1],
                    ["百家号", 30, 88.3],
                    ["今日头条", 52, 96.2],
                    ["小红书", 28, 89.3],
                    ["微博", 41, 91.7],
                    ["B站专栏", 22, 86.4],
                    ["抖音图文", 35, 94.3],
                ]
            )
        elif "字数" in question or "word" in q_lower:
            return (
                ["类型", "平均字数", "文章数量"],
                [
                    ["AI 生成", 1856, 67],
                    ["手动创作", 2341, 23],
                ]
            )
        elif "平台" in question and ("分布" in question or "数量" in question or "多少" in question):
            return (
                ["平台", "发布数量"],
                [
                    ["今日头条", 52],
                    ["知乎", 45],
                    ["微博", 41],
                    ["微信公众号", 38],
                    ["抖音图文", 35],
                    ["百家号", 30],
                    ["小红书", 28],
                    ["B站专栏", 22],
                ]
            )
        elif "趋势" in question or "每天" in question or "每月" in question or "时间" in question:
            return (
                ["日期", "发布数量"],
                [
                    ["2026-02-01", 8],
                    ["2026-02-05", 12],
                    ["2026-02-10", 15],
                    ["2026-02-15", 11],
                    ["2026-02-20", 18],
                    ["2026-02-25", 14],
                    ["2026-03-01", 20],
                ]
            )
        elif "分类" in question or "类型" in question or "category" in q_lower:
            return (
                ["分类", "文章数量"],
                [
                    ["市场分析", 28],
                    ["使用教程", 35],
                    ["新闻解读", 42],
                    ["项目介绍", 18],
                    ["宣传推广", 12],
                    ["行业报告", 9],
                ]
            )
        else:
            # 通用统计
            return (
                ["指标", "数值"],
                [
                    ["总文章数", 90],
                    ["AI 生成", 67],
                    ["手动创作", 23],
                    ["已发布", 78],
                    ["草稿", 12],
                    ["发布任务总数", 291],
                    ["成功任务", 265],
                    ["失败任务", 26],
                ]
            )

    def _build_mock_meta(self, question: str) -> Dict[str, Any]:
        """根据问题内容自动推断 chart_type（用于 use_mock 模式）"""
        q = question
        if "成功率" in q:
            return {"chart_type": "bar", "chart_title": "平台发布成功率对比",
                    "explanation": "展示各平台发布成功率对比",
                    "insight": "成功率越高说明平台对该类内容的接受度越高",
                    "sql": "SELECT platform AS 平台, COUNT(*) AS 发布次数, ROUND(SUM(CASE WHEN status='success' THEN 1.0 ELSE 0 END)/COUNT(*)*100,1) AS 成功率 FROM publish_tasks WHERE created_at >= datetime('now','-30 days') GROUP BY platform ORDER BY 成功率 DESC"}
        elif "字数" in q:
            return {"chart_type": "bar", "chart_title": "AI 生成 vs 手动写作平均字数",
                    "explanation": "对比 AI 生成和手动写作的文章平均字数",
                    "insight": "字数越高通常说明内容更丰富，但也需考虑读者阅读体验",
                    "sql": "SELECT CASE WHEN ai_generated=1 THEN 'AI生成' ELSE '手动写作' END AS 类型, AVG(word_count) AS 平均字数, COUNT(*) AS 文章数量 FROM articles GROUP BY ai_generated"}
        elif "趋势" in q or "每天" in q:
            return {"chart_type": "line", "chart_title": "发布数量趋势",
                    "explanation": "展示最近30天每天发布数量趋势",
                    "insight": "发布频率稳定说明运营节奏良好",
                    "sql": "SELECT date(created_at) AS 日期, COUNT(*) AS 发布数量 FROM publish_tasks WHERE created_at >= datetime('now','-30 days') GROUP BY date(created_at) ORDER BY 日期"}
        elif "分类" in q:
            return {"chart_type": "pie", "chart_title": "内容分类分布",
                    "explanation": "展示各分类文章数量占比",
                    "insight": "内容分类分布均衡说明运营策略多元化",
                    "sql": "SELECT category AS 分类, COUNT(*) AS 文章数量 FROM articles GROUP BY category ORDER BY 文章数量 DESC"}
        else:
            return {"chart_type": "bar", "chart_title": "平台发布数量分布",
                    "explanation": "展示各平台累计发布文章数量",
                    "insight": "发布数量越高的平台是运营重心",
                    "sql": "SELECT platform AS 平台, COUNT(*) AS 发布数量 FROM publish_tasks GROUP BY platform ORDER BY 发布数量 DESC"}

    def query(self, question: str, use_mock: bool = False) -> Dict[str, Any]:
        """
        主入口：自然语言问题 → 完整查询结果
        返回前端可直接使用的结构化数据
        use_mock=True 时跳过 LLM，直接返回演示数据
        """
        start_time = datetime.now()

        # 强制演示模式
        if use_mock:
            mock_meta = self._build_mock_meta(question)
            columns, rows = self._generate_mock_data(question, mock_meta)
            elapsed = (datetime.now() - start_time).total_seconds()
            chart_type = mock_meta.get("chart_type", "bar")
            x_idx, y_idx = 0, 1 if len(columns) > 1 else 0
            return {
                "success": True,
                "question": question,
                "sql": mock_meta.get("sql", "-- 演示模式"),
                "explanation": mock_meta.get("explanation", "演示数据"),
                "insight": mock_meta.get("insight", "这是演示数据，配置 AI 密鑰后可查询实际数据"),
                "elapsed_ms": round(elapsed * 1000),
                "is_mock": True,
                "data": {"columns": columns, "rows": rows, "row_count": len(rows)},
                "chart": {
                    "type": chart_type,
                    "title": mock_meta.get("chart_title", question),
                    "labels": [str(r[x_idx]) for r in rows],
                    "values": [r[y_idx] for r in rows],
                },
            }

        # Step 1: NL → SQL
        sql_meta = self._nl_to_sql(question)
        sql = sql_meta.get("sql", "").strip()

        if not sql:
            return {
                "success": False,
                "question": question,
                "message": "无法将该问题转换为数据库查询，请尝试更具体的问题",
                "sql": "",
                "data": {"columns": [], "rows": []},
                "chart": {"type": "none"},
            }

        # Step 2: 执行 SQL
        is_mock = False
        try:
            columns, rows = self._execute_sql(sql)
            if not rows:
                # 数据库为空，使用演示数据
                columns, rows = self._generate_mock_data(question, sql_meta)
                is_mock = True
        except ValueError as e:
            return {
                "success": False,
                "question": question,
                "message": str(e),
                "sql": sql,
                "data": {"columns": [], "rows": []},
                "chart": {"type": "none"},
            }

        # Step 3: 构建返回结构
        elapsed = (datetime.now() - start_time).total_seconds()
        chart_type = sql_meta.get("chart_type", "table")
        x_field = sql_meta.get("x_field", columns[0] if columns else "")
        y_field = sql_meta.get("y_field", columns[1] if len(columns) > 1 else "")

        # 自动推断 x/y 字段索引
        x_idx = 0
        y_idx = 1 if len(columns) > 1 else 0
        for i, col in enumerate(columns):
            if col == x_field:
                x_idx = i
            if col == y_field:
                y_idx = i

        return {
            "success": True,
            "question": question,
            "sql": sql,
            "explanation": sql_meta.get("explanation", ""),
            "insight": sql_meta.get("insight", ""),
            "elapsed_ms": round(elapsed * 1000),
            "is_mock": is_mock,
            "data": {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
            },
            "chart": {
                "type": chart_type,
                "title": sql_meta.get("chart_title", question),
                "x_field": x_field,
                "y_field": y_field,
                "x_idx": x_idx,
                "y_idx": y_idx,
                "labels": [str(row[x_idx]) for row in rows],
                "values": [row[y_idx] for row in rows],
                # 多系列数据（用于 bar_grouped）
                "series": [
                    {
                        "name": columns[i],
                        "data": [row[i] for row in rows]
                    }
                    for i in range(1, len(columns))
                ] if chart_type == "bar_grouped" else [],
            }
        }


# ─── 预设问题模板 ────────────────────────────────────────────────────────────
PRESET_QUESTIONS = [
    {
        "category": "发布分析",
        "icon": "📊",
        "questions": [
            "过去30天哪个平台的发布成功率最高？",
            "各平台累计发布文章数量分布",
            "最近7天每天的发布数量趋势",
            "发布失败最多的平台是哪个？",
        ]
    },
    {
        "category": "内容分析",
        "icon": "✍️",
        "questions": [
            "AI 生成的文章和手动写的文章哪个字数更多？",
            "各分类文章数量分布",
            "已发布文章 vs 草稿文章的比例",
            "最近创建的10篇文章标题和状态",
        ]
    },
    {
        "category": "运营概览",
        "icon": "🎯",
        "questions": [
            "总体发布成功率是多少？",
            "本月新增文章数量",
            "各平台账号状态概览",
            "热点话题使用情况统计",
        ]
    },
]
