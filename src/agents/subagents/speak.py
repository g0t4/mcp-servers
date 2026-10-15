"""Speech tool: text-to-speech with the user's cloned voice."""

from __future__ import annotations

import os
import sys

from mcp.types import TextContent, Tool

# Add ask-openai.nvim to path so we can import tools.voiced.tts
_ASK_OPENAI_DIR = os.path.expanduser(
    "~/repos/github/g0t4/ask-openai.nvim"
)
if _ASK_OPENAI_DIR not in sys.path:
    sys.path.insert(0, _ASK_OPENAI_DIR)

from tools.voiced.tts import TTSClient, SERVER_VOICE_REF

SPEAK_TOOL_NAME = "speak"

SPEAK_TOOL = Tool(
    name=SPEAK_TOOL_NAME,
    description=(
        "Speak text out loud using text-to-speech (TTS). Uses the user's "
        "cloned voice. The audio is played on the local machine so the user "
        "can hear it. Use this to communicate audibly with the user. Keep the "
        "text concise (1-3 sentences)."
    ),
    inputSchema={
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to speak. Keep it concise (1-3 sentences).",
            },
            "lang": {
                "type": "string",
                "description": "Language code (e.g. 'en', 'zh', 'ja'). Default 'en'.",
                "default": "en",
            },
        },
        "required": ["text"],
        "type": "object",
    },
)


async def speak(text: str, lang: str = "en") -> list[TextContent]:
    """Speak text out loud using TTS."""
    try:
        client = TTSClient()
        client.speak(text, lang=lang, speaker_file=SERVER_VOICE_REF)
        return [TextContent(type="text", text=f"Spoke: {text!r}")]
    except Exception as error:
        return [TextContent(type="text", text=f"ERROR: Failed to speak: {error}")]
