"""
LLM content evaluator — adapted from Junk-Filter's ContentEvaluationAgent.
Strips LangGraph in favor of a simple retry loop. Same prompt, same output.
"""

import json
import re
import logging
from typing import Optional, List, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """/no_think
你是一个专业的内容评估专家。

你需要评估提供的文章，并生成以下结构化JSON格式的评估：
{
    "innovation_score": <0-10整数>,
    "depth_score": <0-10整数>,
    "decision": "<INTERESTING|BOOKMARK|SKIP>",
    "key_concepts": [<字符串数组，最多5个关键概念>],
    "tldr": "<一句话总结，不超过100字>",
    "reasoning": "<简短的推理过程>"
}

评估维度：
1. innovation_score (0-10)：评估内容的创新度和突破性
   - 8-10：真正突破性的发现，具有革命性影响
   - 6-7：有重要的新见解，能推进领域发展
   - 4-5：有一些新的想法，但不够深入
   - 1-3：主要是既有知识的重述

2. depth_score (0-10)：评估内容的深度和严谨性
   - 8-10：深入的学术级别分析，充分的证据支持
   - 6-7：相当深入的讨论，有逻辑支持
   - 4-5：中等深度，基本的论证
   - 1-3：表面级别的讨论

3. decision：决策标准
   - INTERESTING：innovation_score >= 7 AND depth_score >= 6（高价值内容）
   - BOOKMARK：innovation_score >= 5 OR depth_score >= 5（中等价值）
   - SKIP：其他情况（低价值内容）

请严格按照JSON格式返回，不包含任何其他文本。"""


class ContentEvaluator:
    """Evaluate article quality via LLM. Retries on parse failure."""

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 500,
        max_retries: int = 2,
    ):
        if not api_key:
            raise ValueError("API key is required")

        llm_kwargs = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "api_key": api_key,
        }
        if api_base:
            llm_kwargs["base_url"] = api_base

        self.llm = ChatOpenAI(**llm_kwargs)
        self.model = model
        self.max_retries = max_retries
        logger.info(f"ContentEvaluator initialized: model={model}")

    def evaluate(
        self,
        title: str,
        content: str,
        url: str = "",
    ) -> Dict[str, Any]:
        """
        Evaluate a single article.

        Returns dict with: innovation_score, depth_score, decision,
        key_concepts, tldr, reasoning.
        Raises RuntimeError on persistent failure.
        """
        user_prompt = f"""请评估以下内容：

标题：{title}

内容：{content[:3000]}

URL：{url}

请严格返回JSON格式，不添加任何解释文字。"""

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]

        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                response = self.llm.invoke(messages)
                text = response.content or ""
                if not text:
                    # Some reasoning models put output in reasoning_content
                    reasoning = (
                        response.additional_kwargs.get("reasoning_content")
                        or response.additional_kwargs.get("reasoning")
                        or ""
                    )
                    text = reasoning

                return self._parse(text, title)

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Evaluation attempt {attempt + 1}/{self.max_retries + 1} failed: {last_error}"
                )

        raise RuntimeError(f"Evaluation failed after {self.max_retries + 1} attempts: {last_error}")

    def evaluate_batch(self, items: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Evaluate multiple articles. Failures return a default SKIP result."""
        results = []
        for item in items:
            try:
                result = self.evaluate(
                    item.get("title", ""),
                    item.get("content", ""),
                    item.get("url", ""),
                )
                results.append(result)
            except Exception as e:
                logger.error(f"Batch item failed: {e}")
                results.append({
                    "innovation_score": 5,
                    "depth_score": 5,
                    "decision": "SKIP",
                    "key_concepts": [],
                    "tldr": item.get("title", "")[:100],
                    "reasoning": f"评估失败: {e}",
                })
        return results

    @staticmethod
    def _parse(text: str, title: str) -> Dict[str, Any]:
        """Extract and validate JSON from LLM response."""
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON found in LLM response")

        data = json.loads(json_match.group(0))

        result = {
            "innovation_score": max(0, min(10, int(data.get("innovation_score", 5)))),
            "depth_score": max(0, min(10, int(data.get("depth_score", 5)))),
            "decision": data.get("decision", "BOOKMARK").upper(),
            "key_concepts": data.get("key_concepts", [])[:5],
            "tldr": data.get("tldr", "")[:200],
            "reasoning": data.get("reasoning", "")[:500],
        }

        if result["decision"] not in ("INTERESTING", "BOOKMARK", "SKIP"):
            result["decision"] = "BOOKMARK"

        logger.info(
            f"Evaluated: {title[:60]} → "
            f"innovation={result['innovation_score']} depth={result['depth_score']} "
            f"decision={result['decision']}"
        )
        return result
