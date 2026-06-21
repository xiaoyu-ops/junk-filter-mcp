# Junk Filter MCP

> 智能 RSS 内容过滤 MCP 服务 — LLM 驱动的文章价值评估，任何 AI Agent 都能接入。

## 这是什么？

Junk Filter 是一个轻量级的 MCP (Model Context Protocol) 服务器，提供 RSS 抓取 + LLM 评估 + 结果查询能力。它从 [Junk Filter 全栈项目](https://github.com/xiaoyu-ops/Junk-Filter) 剥离而来，去掉了 Vue 前端、Go 网关、Redis、PostgreSQL，只保留 **评估引擎核心**。

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/xiaoyu-ops/junk-filter-mcp.git
cd junk-filter-mcp

# 2. 安装依赖
pip install -e .

# 3. 配置
# 编辑 config.yaml，填入 LLM API Key
# 或设环境变量: JUNK_FILTER_API_KEY=sk-xxx

# 4. 测试
python server.py   # 启动 MCP stdio 服务
```

## 配置 Hermes Agent

将 `hermes-mcp-config.yaml` 的内容合并到 `~/.hermes/config.yaml` 的 `mcp_servers` 下：

```bash
cat hermes-mcp-config.yaml >> ~/.hermes/config.yaml
```

重启 Hermes 后即可通过 `native-mcp` 调用所有工具。

## 可用工具

| Tool | 说明 |
|------|------|
| `add_feed` | 注册新 RSS 源 |
| `list_feeds` | 列出所有 RSS 源 |
| `fetch_feed` | 抓取指定源的最新文章 |
| `evaluate_content` | LLM 评估单篇文章 |
| `evaluate_feed` | 批量评估一个源的所有未评估文章 |
| `top_articles` | 查询高分文章 |
| `search_articles` | 关键词搜索文章 |

## 评估维度

- **创新度** (0-10)：内容的新颖性和突破性
- **深度** (0-10)：分析的严谨性和深入程度
- **决策**：INTERESTING（高价值）/ BOOKMARK（中等）/ SKIP（低价值）

## 环境变量

| 变量 | 说明 |
|------|------|
| `JUNK_FILTER_API_KEY` | LLM API Key |
| `JUNK_FILTER_API_BASE` | API Base URL（中转站） |
| `JUNK_FILTER_MODEL` | 模型名（默认 gpt-4o） |
| `JUNK_FILTER_DB_PATH` | SQLite 数据库路径 |

## 与全栈版的关系

```
Junk-Filter (全栈)          junk-filter-mcp (MCP)
──────────────              ───────────────────
Vue 3 前端            ✘     无前端
Go API 网关           ✘     无
PostgreSQL            ✘     SQLite
Redis Stream          ✘     同步调用
Docker 编排           ✘     单进程
ContentEvaluator      ✓     ContentEvaluator（简化）
Telegram Bot          ✘     通过 Hermes 推送
```

## License

MIT
