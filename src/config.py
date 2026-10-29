from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional

from .constants import (
    DATA_DIR,
    DEFAULT_BACKEND,
    DEFAULT_LLM_TIMEOUT_SEC,
    DEFAULT_OPENAI_MODEL,
    ENV_FILE_PATH,
    SQLITE_DB_PATH,
)


def load_dotenv_file(dotenv_path: Path = ENV_FILE_PATH) -> None:
    if not dotenv_path.is_file():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        k = key.strip()
        v = value.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


@dataclass
class Settings:
    backend: str
    use_live_llm: bool
    openai_api_key: str
    openai_model: str
    claude_cli_path: Optional[str]
    llm_timeout_sec: int
    data_dir: Path
    sqlite_db_path: Path


def get_settings() -> Settings:
    load_dotenv_file()
    return Settings(
        backend=os.environ.get("SUPPORT_AGENT_BACKEND", DEFAULT_BACKEND).strip().lower(),
        use_live_llm=os.environ.get("USE_LIVE_LLM", "0").strip().lower() in ("1", "true", "yes"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        openai_model=os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip(),
        claude_cli_path=os.environ.get("CLAUDE_CLI_PATH", "").strip() or None,
        llm_timeout_sec=int(os.environ.get("LLM_TIMEOUT_SEC", str(DEFAULT_LLM_TIMEOUT_SEC))),
        data_dir=Path(os.environ.get("SUNNYSTEP_DATA_DIR", str(DATA_DIR))),
        sqlite_db_path=Path(os.environ.get("SUNNYSTEP_SQLITE_DB", str(SQLITE_DB_PATH))),
    )

