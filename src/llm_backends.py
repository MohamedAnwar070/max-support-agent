import glob
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import List, Optional
import urllib.request

from .config import get_settings
from .constants import OPENAI_CHAT_COMPLETIONS_URL


def find_claude_cli_binary() -> Optional[str]:
    settings = get_settings()
    if settings.claude_cli_path and os.path.isfile(settings.claude_cli_path):
        return settings.claude_cli_path

    which_claude = shutil.which("claude")
    if which_claude:
        return which_claude

    home = Path.home()
    search_patterns = [
        str(home / ".antigravity-ide" / "extensions" / "anthropic.claude-code-*" / "resources" / "native-binary" / "claude.exe"),
        str(home / ".vscode" / "extensions" / "anthropic.claude-code-*" / "resources" / "native-binary" / "claude.exe"),
        str(home / ".cursor" / "extensions" / "anthropic.claude-code-*" / "resources" / "native-binary" / "claude.exe"),
        str(home / ".local" / "bin" / "claude*"),
    ]
    candidates: List[str] = []
    for pattern in search_patterns:
        candidates.extend(glob.glob(pattern))

    if candidates:
        candidates.sort(reverse=True)
        return candidates[0]

    return None


class ClaudeCodeCLIBackend:
    """Runs `claude -p` as a pure text model: no built-in tools, no MCP servers, no user settings,
    prompt over stdin. Customer text therefore cannot make the CLI read files or run commands."""

    def __init__(self, binary_path: Optional[str] = None, timeout_sec: Optional[int] = None) -> None:
        settings = get_settings()
        self.binary_path = binary_path or find_claude_cli_binary()
        self.timeout_sec = timeout_sec or settings.llm_timeout_sec
        self.label = "claude_code_pro_cli"

    def is_available(self) -> bool:
        return bool(self.binary_path and os.path.isfile(self.binary_path))

    def complete(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        if not self.is_available() or not self.binary_path:
            return None
        cmd = [
            self.binary_path,
            "-p",
            "--tools", "",
            "--strict-mcp-config",
            "--setting-sources", "",
            "--no-session-persistence",
            "--output-format", "text",
            "--system-prompt", system_prompt,
        ]
        try:
            proc = subprocess.run(
                cmd,
                input=user_prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_sec,
                cwd=tempfile.gettempdir(),
            )
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None


class OpenAIBackend:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_sec: Optional[int] = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.openai_api_key
        self.model = model or settings.openai_model
        self.timeout_sec = timeout_sec or settings.llm_timeout_sec
        self.label = f"openai:{self.model}"

    def is_available(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("sk-your-"))

    def complete(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        if not self.is_available():
            return None
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = urllib.request.Request(
            OPENAI_CHAT_COMPLETIONS_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return (body["choices"][0]["message"].get("content") or "").strip() or None
        except Exception:
            return None
