"""
MCP stdio server — exposes Junk Filter tools for any AI agent.
"""

import sys
import logging
from mcp.server import Server, NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from config_loader import load_config, get_llm_config
from db import DB
from agents.content_evaluator import ContentEvaluator

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("junk-filter-mcp")

# ── Init ───────────────────────────────────────────────────────────
cfg = load_config()
db = DB(cfg["database"]["path"])
llm_cfg = get_llm_config(cfg)
evaluator = ContentEvaluator(**llm_cfg)
server = Server("junk-filter-mcp")


# ── Tool definitions ───────────────────────────────────────────────

TOOLS = [
    Tool(
        name="add_feed",
        description="Register a new RSS feed source. Returns the feed ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "RSS feed URL"},
                "name": {"type": "string", "description": "Human-readable name for this source"},
            },
            "required": ["url", "name"],
        },
    ),
    Tool(
        name="list_feeds",
        description="List all registered RSS feed sources.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="fetch_feed",
        description="Fetch the latest articles from a registered RSS source. Deduplicates by URL. Returns newly discovered articles.",
        inputSchema={
            "type": "object",
            "properties": {
                "feed_id": {"type": "integer", "description": "Feed ID from add_feed or list_feeds"},
                "max_articles": {"type": "integer", "description": "Max articles to return (default 20)", "default": 20},
            },
            "required": ["feed_id"],
        },
    ),
    Tool(
        name="evaluate_content",
        description="Evaluate a single article using LLM. Returns innovation_score (0-10), depth_score (0-10), decision (INTERESTING/BOOKMARK/SKIP), TLDR summary, and key concepts.",
        inputSchema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Article title"},
                "content": {"type": "string", "description": "Article body text"},
                "url": {"type": "string", "description": "Article URL (optional)"},
            },
            "required": ["title", "content"],
        },
    ),
    Tool(
        name="evaluate_feed",
        description="Fetch and evaluate all unevaluated articles from a feed source.",
        inputSchema={
            "type": "object",
            "properties": {
                "feed_id": {"type": "integer", "description": "Feed ID to evaluate"},
                "limit": {"type": "integer", "description": "Max articles to evaluate (default 10)", "default": 10},
            },
            "required": ["feed_id"],
        },
    ),
    Tool(
        name="top_articles",
        description="Query high-value evaluated articles. Filters by minimum score and optional decision type.",
        inputSchema={
            "type": "object",
            "properties": {
                "min_score": {"type": "integer", "description": "Minimum average score (0-10, default 6)", "default": 6},
                "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
                "decision": {"type": "string", "description": "Filter by decision: INTERESTING, BOOKMARK, or SKIP"},
            },
        },
    ),
    Tool(
        name="search_articles",
        description="Search articles by keyword in title and content.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword"},
                "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
            },
            "required": ["query"],
        },
    ),
]


# ── Tool handlers ──────────────────────────────────────────────────

@server.list_tools()
async def handle_list_tools():
    return TOOLS


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    try:
        if name == "add_feed":
            feed_id = db.add_feed(arguments["url"], arguments["name"])
            return [TextContent(type="text", text=f"Feed registered. ID: {feed_id}")]

        elif name == "list_feeds":
            feeds = db.list_feeds()
            if not feeds:
                return [TextContent(type="text", text="No feeds registered. Use add_feed to add one.")]
            lines = [f"{'ID':<5} {'Name':<30} {'Status':<10} {'Last Fetch'}", "-" * 60]
            for f in feeds:
                last = f.get("last_fetch_at") or "never"
                lines.append(f"{f['id']:<5} {f['name']:<30} {f.get('status','?'):<10} {last}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "fetch_feed":
            feed_id = arguments["feed_id"]
            max_articles = arguments.get("max_articles", 20)
            articles = _fetch_rss(feed_id, max_articles)
            if not articles:
                return [TextContent(type="text", text="No new articles found.")]
            summary = f"Fetched {len(articles)} new articles from feed {feed_id}:\n\n"
            for i, a in enumerate(articles, 1):
                summary += f"{i}. [{a['title']}]({a['url']})\n"
            return [TextContent(type="text", text=summary)]

        elif name == "evaluate_content":
            result = evaluator.evaluate(
                title=arguments["title"],
                content=arguments["content"],
                url=arguments.get("url", ""),
            )
            return [TextContent(type="text", text=_format_eval_result(result))]

        elif name == "evaluate_feed":
            feed_id = arguments["feed_id"]
            limit = arguments.get("limit", 10)
            # First fetch
            _fetch_rss(feed_id, max_articles=limit)
            # Then evaluate unevaluated
            articles = db.get_unevaluated(feed_id, limit=limit)
            if not articles:
                return [TextContent(type="text", text="No unevaluated articles.")]

            results = []
            for art in articles:
                try:
                    eval_result = evaluator.evaluate(
                        title=art["title"],
                        content=art["content"],
                        url=art.get("url", ""),
                    )
                    db.save_evaluation(
                        article_id=art["id"],
                        innovation_score=eval_result["innovation_score"],
                        depth_score=eval_result["depth_score"],
                        decision=eval_result["decision"],
                        tldr=eval_result["tldr"],
                        key_concepts=eval_result["key_concepts"],
                        reasoning=eval_result["reasoning"],
                        model=llm_cfg["model"],
                    )
                    results.append((art, eval_result))
                except Exception as e:
                    logger.error(f"Failed evaluating article {art['id']}: {e}")

            db.update_feed_fetch(feed_id, success=True)

            summary = f"Evaluated {len(results)}/{len(articles)} articles:\n\n"
            for art, ev in results:
                icon = {"INTERESTING": "🔥", "BOOKMARK": "📌", "SKIP": "⏭️"}.get(ev["decision"], "❓")
                summary += f"{icon} [{ev['decision']}] **{art['title']}**\n"
                summary += f"   创新度={ev['innovation_score']} 深度={ev['depth_score']} | {ev['tldr']}\n\n"
            return [TextContent(type="text", text=summary)]

        elif name == "top_articles":
            rows = db.top_articles(
                min_score=arguments.get("min_score", 6),
                limit=arguments.get("limit", 10),
                decision=arguments.get("decision"),
            )
            if not rows:
                return [TextContent(type="text", text="No matching articles found.")]
            lines = []
            for r in rows:
                icon = {"INTERESTING": "🔥", "BOOKMARK": "📌", "SKIP": "⏭️"}.get(r.get("decision", ""), "❓")
                lines.append(
                    f"{icon} [{r['decision']}] **{r['title']}** "
                    f"(创新={r['innovation_score']} 深度={r['depth_score']})\n"
                    f"   TLDR: {r.get('tldr', 'N/A')}\n"
                )
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "search_articles":
            rows = db.search_articles(
                query=arguments["query"],
                limit=arguments.get("limit", 20),
            )
            if not rows:
                return [TextContent(type="text", text=f"No articles matching '{arguments['query']}'.")]
            lines = [f"Found {len(rows)} matches for '{arguments['query']}':", ""]
            for r in rows:
                score_str = ""
                if r.get("innovation_score") is not None:
                    score_str = f" 创新={r['innovation_score']} 深度={r['depth_score']} [{r.get('decision','?')}]"
                lines.append(f"- **{r['title']}**{score_str}")
            return [TextContent(type="text", text="\n".join(lines))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        logger.error(f"Tool '{name}' failed: {e}", exc_info=True)
        return [TextContent(type="text", text=f"Error: {e}")]


# ── RSS fetching ───────────────────────────────────────────────────

def _fetch_rss(feed_id: int, max_articles: int = 20) -> list:
    """Fetch RSS feed and store new articles. Returns list of new articles."""
    import feedparser

    feed = db.get_feed(feed_id)
    if not feed:
        raise ValueError(f"Feed not found: {feed_id}")

    try:
        parsed = feedparser.parse(feed["url"])
    except Exception as e:
        db.update_feed_fetch(feed_id, success=False)
        raise RuntimeError(f"Failed to parse RSS feed: {e}")

    if parsed.bozo and not parsed.entries:
        db.update_feed_fetch(feed_id, success=False)
        raise RuntimeError(f"RSS feed is broken: {getattr(parsed.bozo_exception, 'message', str(parsed.bozo_exception))}")

    new_articles = []
    for entry in parsed.entries[:max_articles]:
        link = entry.get("link", "")
        if not link:
            continue
        title = entry.get("title", "Untitled")
        # Extract text content
        content = ""
        if hasattr(entry, "content"):
            content = entry.content[0].value if entry.content else ""
        if not content:
            content = entry.get("summary", entry.get("description", ""))
        author = entry.get("author", "")
        published = entry.get("published", "")

        # Skip short content
        if len(content.strip()) < 100:
            continue

        article_id = db.upsert_article(feed_id, link, title, content, author, published)
        if article_id > 0:
            new_articles.append({"id": article_id, "title": title, "url": link})

    db.update_feed_fetch(feed_id, success=True)
    logger.info(f"Feed {feed_id}: {len(new_articles)} new, {parsed.entries[:max_articles].__len__() - len(new_articles)} dupes")
    return new_articles


def _format_eval_result(result: dict) -> str:
    """Format evaluation result for display."""
    icon = {"INTERESTING": "🔥", "BOOKMARK": "📌", "SKIP": "⏭️"}.get(result["decision"], "❓")
    concepts = ", ".join(result.get("key_concepts", []))
    return (
        f"{icon} **{result['decision']}**\n"
        f"   创新度: {result['innovation_score']}/10\n"
        f"   深度:   {result['depth_score']}/10\n"
        f"   TLDR:  {result['tldr']}\n"
        f"   关键概念: {concepts}\n"
        f"   推理: {result['reasoning']}"
    )


# ── Entry ──────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            NotificationOptions(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
