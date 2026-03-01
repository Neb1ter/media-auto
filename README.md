# 🚀 Media-Auto — 自媒体运营自动化平台

> AI 驱动 · 多平台 · 高效运营 | 一站式内容创作与多平台自动发布系统

[![GitHub Pages](https://img.shields.io/badge/Demo-GitHub%20Pages-purple?style=flat-square&logo=github)](https://neb1ter.github.io/media-auto/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-green?style=flat-square&logo=python)](https://python.org)

## 🌐 在线演示

**👉 [https://neb1ter.github.io/media-auto/](https://neb1ter.github.io/media-auto/)**

> 演示版本内置 Mock 数据，无需后端即可体验所有界面功能。完整自动化发布功能需本地/服务器部署。

---

## ✨ 功能特性

| 功能模块 | 描述 |
|---------|------|
| 📊 **运营看板** | 实时展示文章数、发布成功率、平台账号状态等核心指标 |
| ✍️ **AI 智能创作** | 输入主题，一键生成标题候选、文章大纲、完整正文（支持 Markdown） |
| 🔄 **多平台适配** | 针对知乎、微信公众号、百家号、头条、小红书、微博自动调整内容风格 |
| 📚 **文章库管理** | 统一管理所有草稿和已发布文章，支持搜索、筛选、编辑 |
| 🌐 **自动化发布** | 基于 Playwright 浏览器自动化，一次登录，后续自动发布 |
| ⏰ **定时发布** | 设置发布时间和重复频率，让内容在最佳时机触达用户 |
| ⚙️ **灵活配置** | 支持接入任意 OpenAI 兼容的 AI 模型（GPT、Claude、Gemini、国产大模型等） |

## 🏗️ 技术架构

```
media-auto/
├── index.html          # 前端单页应用（Vue.js 3 + Tailwind CSS）
│                       # ✅ 可直接通过 GitHub Pages 预览
├── backend/
│   ├── main.py         # FastAPI 后端入口 & API 路由
│   ├── models.py       # 数据库模型（SQLAlchemy + SQLite）
│   ├── ai_creator.py   # AI 内容创作模块（OpenAI API）
│   └── publisher.py    # 多平台发布模块（Playwright 浏览器自动化）
├── docs/
│   ├── README.md       # 详细使用文档
│   └── PLATFORM_GUIDE.md  # 各平台企业号申请指南
└── requirements.txt    # Python 依赖
```

## 🚀 本地部署

### 前提条件

- Python 3.11+
- pip

### 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/Neb1ter/media-auto.git
cd media-auto

# 2. 安装依赖
pip install -r requirements.txt

# 3. 安装 Playwright 浏览器
playwright install chromium

# 4. 配置环境变量
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"  # 可选，默认使用 OpenAI

# 5. 启动后端服务
cd backend && uvicorn main:app --host 0.0.0.0 --port 8001 --reload

# 6. 访问系统
# 前端：直接用浏览器打开 index.html
# API 文档：http://localhost:8001/docs
```

## 📖 使用指南

### 第一步：登录平台账号

1. 打开系统，进入 **[发布管理]** 页面
2. 选择目标平台，点击 **[去登录]**
3. 在弹出的浏览器窗口中**手动完成一次登录**（扫码或账密）
4. 登录成功后，系统自动保存 Cookie，后续发布无需再次登录

### 第二步：AI 创作文章

1. 进入 **[AI 创作]** 页面
2. 输入**文章主题**，选择**目标平台风格**
3. 依次点击：生成标题 → 选择标题 → 生成大纲 → 一键生成完整文章
4. 在编辑器中审阅并修改内容

### 第三步：发布文章

1. 点击 **[发布文章]** 按钮
2. 勾选已登录的目标平台（支持同时发布到多个平台）
3. 点击 **[确认发布]**，系统自动完成发布流程

## 🙏 致谢

本项目基于以下优秀开源项目构建：

- **[dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload)** — 多平台自动发布核心引擎（⭐ 8,600+）
- **[ZuckerChen/ss-media-tools](https://github.com/ZuckerChen/ss-media-tools)** — AI 内容创作工具

## 📄 License

MIT License © 2026 Neb1ter
