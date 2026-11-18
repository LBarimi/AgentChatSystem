"""Shared connection settings and task-tool guidance for Relay clients."""

from relay.prompts import prompt_catalog


DEFAULT_WAIT_SECONDS = 40
MAX_WAIT_SECONDS = 40
LEGACY_MAX_WAIT_SECONDS = 60
MCP_TOOL_TIMEOUT_SECONDS = 90
MCP_VERSION = '7.0.0'

_KOREAN_PROMPTS = prompt_catalog('ko')
LEGACY_PNG_PROMPT = _KOREAN_PROMPTS['legacy_png'] + ' '
WORK_TOOLS_PROMPT = _KOREAN_PROMPTS['work_tools'] + ' '

WORK_TOOLS_INSTRUCTIONS = (
    'Relay is the messaging transport, not a restriction on task tools. '
    'For authorized work, use the image generation/editing, file, terminal, web, browser, '
    'and other MCP tools actually available in this session within existing permissions. '
    'Do not defer work merely because it needs a non-Relay tool. '
    'Do not claim an image was generated if no image-generation tool is available or the call failed. '
)
