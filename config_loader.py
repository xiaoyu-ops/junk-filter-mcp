"""
Config: reads config.yaml, overridable by env vars for container deployments.
"""

import os
import yaml
from pathlib import Path
from typing import Optional, Dict, Any


def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    """Load config from YAML file. Env vars override key fields."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    # Env var overrides
    cfg["llm"]["api_key"] = os.getenv("JUNK_FILTER_API_KEY") or cfg.get("llm", {}).get("api_key", "")
    cfg["llm"]["api_base"] = os.getenv("JUNK_FILTER_API_BASE") or cfg.get("llm", {}).get("api_base", "")
    cfg["llm"]["model"] = os.getenv("JUNK_FILTER_MODEL") or cfg.get("llm", {}).get("model", "gpt-4o")

    db_path = os.getenv("JUNK_FILTER_DB_PATH") or cfg.get("database", {}).get("path", "junk_filter.db")
    cfg.setdefault("database", {})["path"] = db_path

    return cfg


def get_llm_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Extract LLM config dict from loaded config."""
    llm = cfg.get("llm", {})
    return {
        "model": llm.get("model", "gpt-4o"),
        "api_key": llm.get("api_key", ""),
        "api_base": llm.get("api_base", ""),
        "temperature": llm.get("temperature", 0.7),
        "max_tokens": llm.get("max_tokens", 500),
    }
