"""
Demo Display & Live Transcript Console for Innoventix Hub Voice Agent.

Provides a clean, professional, video-recording-ready terminal interface:
- Silences noisy library/debug logs on the console and routes them to `logs/call_debug.log`.
- Displays a clean visual transcript showing what the caller says, Clara's responses,
  live processing status, and business tool actions in real time.
"""

import logging
import os
import sys
import time
from datetime import datetime
from loguru import logger

# Reconfigure stdout/stderr encoding on Windows to safely handle any characters
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Terminal ANSI Color & Style Codes (works in modern PowerShell, Windows Terminal, Linux, macOS)
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
BLUE = "\033[94m"
DIM = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


DEFAULT_LOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "logs", "call_debug.log"
)


def setup_demo_logging(log_filename: str = DEFAULT_LOG_PATH) -> None:
    """Configures logging for demo video recording:
    - Detailed technical debug logs are saved to a file (`logs/call_debug.log`).
    - Console is kept completely clean, showing only the live conversation transcript."""
    log_dir = os.path.dirname(log_filename)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # Reconfigure loguru
    logger.remove()
    logger.add(
        log_filename,
        rotation="20 MB",
        retention="5 days",
        level="DEBUG",
        encoding="utf-8",
        backtrace=True,
        diagnose=True,
    )
    # Console only gets critical errors if something breaks
    logger.add(sys.stderr, level="ERROR")

    # Silence noisy standard library loggers from polluting the terminal
    for noisy_logger in [
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "fastapi",
        "asyncio",
        "aiohttp",
        "aiohttp.access",
        "websockets",
        "pipecat",
    ]:
        l = logging.getLogger(noisy_logger)
        l.setLevel(logging.WARNING)


class DemoDisplay:
    """Manages clean, professional console transcript output during live calls."""

    _last_caller_text = ""
    _last_bot_text = ""

    @classmethod
    def banner(cls, phone_number: str = "+1 (855) 501-0702", provider: str = "Telnyx"):
        print(f"\n{BOLD}{CYAN}+==============================================================================+{RESET}")
        print(f"{BOLD}{CYAN}|                      INNOVENTIX HUB  *  AI VOICE AGENT                       |{RESET}")
        print(f"{BOLD}{CYAN}|     Agent: Clara (AI Concierge)    |    Telephony: {provider:<14}            |{RESET}")
        print(f"{BOLD}{CYAN}|     Status: Online & Ready         |    Number: {phone_number:<17}       |{RESET}")
        print(f"{BOLD}{CYAN}+==============================================================================+{RESET}\n")
        print(f"{DIM}Ready for incoming calls. Dial {phone_number} to start demo.{RESET}\n")

    @classmethod
    def call_started(cls, caller_phone: str | None = None, call_id: str | None = None):
        cls._last_caller_text = ""
        cls._last_bot_text = ""
        phone = caller_phone or "Unknown Number"
        short_id = (call_id or "")[-8:] if call_id else ""

        print(f"\n{BOLD}{GREEN}+------------------------------------------------------------------------------+{RESET}")
        print(f"{BOLD}{GREEN}|  >> INCOMING CALL CONNECTED                                                  |{RESET}")
        print(f"{BOLD}{GREEN}|  Caller: {phone:<22}                                    |{RESET}")
        print(f"{BOLD}{GREEN}|  Agent:  Clara (Innoventix Hub Voice Concierge)                              |{RESET}")
        if short_id:
            print(f"{BOLD}{GREEN}|  Session ID: {short_id:<20}                                            |{RESET}")
        print(f"{BOLD}{GREEN}+------------------------------------------------------------------------------+{RESET}\n")

    @classmethod
    def caller_speaking(cls):
        """Called when user starts speaking."""
        pass

    @classmethod
    def caller_said(cls, text: str):
        """Displays transcribed caller speech in the live transcript."""
        cleaned = text.strip()
        if not cleaned or cleaned == cls._last_caller_text:
            return
        cls._last_caller_text = cleaned
        print(f"{DIM}[{_timestamp()}]{RESET} {BOLD}{GREEN}Caller >{RESET} \"{cleaned}\"")

    @classmethod
    def bot_thinking(cls):
        """Shows that the bot is processing and formulating a response."""
        print(f"{DIM}[{_timestamp()}]{RESET} {MAGENTA}[Processing] Clara is thinking...{RESET}")

    @classmethod
    def bot_said(cls, text: str):
        """Displays the bot's spoken response."""
        cleaned = text.strip()
        if not cleaned or cleaned == cls._last_bot_text:
            return
        cls._last_bot_text = cleaned
        print(f"{DIM}[{_timestamp()}]{RESET} {BOLD}{CYAN}Clara  >{RESET} \"{cleaned}\"")

    @classmethod
    def tool_action(cls, description: str):
        """Displays a business action/tool call being executed."""
        print(f"{DIM}[{_timestamp()}]{RESET} {YELLOW}[Action] {description}{RESET}")

    @classmethod
    def tool_result(cls, description: str):
        """Displays the result of an executed action/tool."""
        print(f"{DIM}[{_timestamp()}]{RESET} {BLUE}[Result] {description}{RESET}")

    @classmethod
    def call_ended(cls, duration_sec: float = 0.0, recording_path: str | None = None):
        """Displays a clean summary box when the call concludes."""
        dur_str = f"{int(duration_sec // 60)}m {int(duration_sec % 60)}s" if duration_sec >= 60 else f"{int(duration_sec)}s"
        rec_display = os.path.basename(recording_path) if recording_path else "Saved in call_recordings/"

        print(f"\n{BOLD}{CYAN}+------------------------------------------------------------------------------+{RESET}")
        print(f"{BOLD}{CYAN}|  >> CALL COMPLETED                                                           |{RESET}")
        print(f"{BOLD}{CYAN}|  Duration:  {dur_str:<20}                                             |{RESET}")
        print(f"{BOLD}{CYAN}|  Recording: {rec_display:<45}    |{RESET}")
        print(f"{BOLD}{CYAN}+------------------------------------------------------------------------------+{RESET}\n")
        print(f"{DIM}Ready for next call...{RESET}\n")
