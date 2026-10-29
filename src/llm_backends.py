import glob
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional
import urllib.request

from .config import get_settings
from .constants import OPENAI_CHAT_COMPLETIONS_URL, SYSTEM_PROMPT


def _clean_llm_output(raw_text: str) -> str:
    lines = [
        line
        for line in raw_text.strip().splitlines()
        if not line.startswith("Warning: no stdin data received")
    ]
    text = "\n".join(lines).strip()
    if "\n---" in text:
        text = text.split("\n---", 1)[0].strip()
    text = re.sub(r"^\*\*Reply:\*\*\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^Reply:\s*", "", text, flags=re.IGNORECASE).strip()
    return text


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
    def __init__(self, binary_path: Optional[str] = None, timeout_sec: Optional[int] = None) -> None:
        settings = get_settings()
        self.binary_path = binary_path or find_claude_cli_binary()
        self.timeout_sec = timeout_sec or settings.llm_timeout_sec

    def is_available(self) -> bool:
        return bool(self.binary_path and os.path.isfile(self.binary_path))

    def generate_grounded_reply(
        self,
        user_message: str,
        tool_records: List[Dict[str, Any]],
        decision_type: str,
        fallback_reply: str,
    ) -> str:
        if not self.is_available() or not self.binary_path:
            return fallback_reply

        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"CUSTOMER MESSAGE:\n{user_message}\n\n"
            f"DECISION TYPE: {decision_type}\n"
            f"TOOL EXECUTION RESULTS (JSON):\n{json.dumps(tool_records, indent=2)}\n\n"
            f"REFERENCE DRAFT:\n{fallback_reply}\n\n"
            "Write ONLY the direct customer support reply in 2-4 concise sentences. "
            "Include all exact IDs (ORD-*, REF-*, CAN-*, ESC-*, POL-*, REQ-*, PROD-*), dollar amounts, and tracking numbers present in the reference draft. "
            "Do NOT include any developer notes, pipeline commentary, or '---' dividers."
        )
        try:
            proc = subprocess.run(
                [self.binary_path, "-p", prompt],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_sec,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                cleaned = _clean_llm_output(proc.stdout)
                if cleaned:
                    return cleaned
        except Exception:
            pass
        return fallback_reply


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

    def is_available(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("sk-your-"))

    def generate_grounded_reply(
        self,
        user_message: str,
        tool_records: List[Dict[str, Any]],
        decision_type: str,
        fallback_reply: str,
    ) -> str:
        if not self.is_available():
            return fallback_reply

        payload = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Customer message: {user_message}\n\n"
                        f"Decision type: {decision_type}\n"
                        f"Executed tool results: {json.dumps(tool_records)}\n\n"
                        f"Verified facts to include: {fallback_reply}\n\n"
                        "Respond directly to the customer in 2-4 concise sentences, preserving all exact IDs (ORD-*, REF-*, CAN-*, ESC-*, POL-*, REQ-*, PROD-*), prices, and tracking numbers."
                    ),
                },
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
                content = body["choices"][0]["message"].get("content", "").strip()
                if content:
                    return _clean_llm_output(content)
        except Exception:
            pass
        return fallback_reply
