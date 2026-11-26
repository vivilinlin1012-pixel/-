# CineFlow Studio

面向 AI 短片创作的本地素材匹配和故事板制作工具（v0.1）。

功能：创建项目、导入剧本文字、批量上传人物/环境/关键图片、自动拆解镜头、AI 辅助关键图匹配（可选 OpenAI 兼容多模态模型）、逐镜头审核编辑、导出 ZIP 素材包。

## 安装

需要 Python 3.10+。

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

打开 http://127.0.0.1:8000

无需 Node.js。图片与数据库默认保存在本地 storage/ 下，storage/ 已被 .gitignore 排除。当前仓库为公开仓库，请勿提交未公开的剧本、肖像或商业素材。

## 智能匹配说明

- 未配置多模态模型：仅使用剧本与文件名线索、关键图上传顺序生成**低置信度候选**，绝不冒充图像识别。
- 可选配置支持图像输入的 OpenAI 兼容 Chat Completions API：

```bash
export CINEFLOW_AI_BASE_URL="https://provider.example/v1"
export CINEFLOW_AI_API_KEY="secret"
export CINEFLOW_AI_MODEL="vision-model"
```

- 配置完成后可分析关键图并匹配镜头，所有结果依然需要人工审核。请自行确认模型供应商的费用与图片隐私条款。
- 原剧本台词不会被 AI 自动改写。

## 版本边界

v0.1 专注于素材导入与故事板；不包括向视频生成平台自动提交任务。复杂扫描 PDF 需先 OCR。镜头结构和 AI 匹配都可能需要人工纠正。
