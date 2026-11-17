"""Shared connection settings and task-tool guidance for Relay clients."""

DEFAULT_WAIT_SECONDS = 40
MAX_WAIT_SECONDS = 40
LEGACY_MAX_WAIT_SECONDS = 60
MCP_TOOL_TIMEOUT_SECONDS = 90
MCP_VERSION = '7.0.0'

LEGACY_PNG_PROMPT = (
    'reply 도구 목록에 attachments 인자가 없어도 PNG 전송을 보류하지 마. '
    '받은 메시지의 channel.shared 폴더 안에 PNG를 저장하거나 복사하고, '
    'reply의 body 마지막 줄에 [[attach_png:이미지.png]]를 넣으면 서버가 실제 PNG 첨부로 변환해. '
    '하위 폴더는 [[attach_png:images/이미지.png]]처럼 shared 기준 상대 경로를 써. '
    '여러 장은 각 줄에 하나씩 넣어. 첨부 지시 줄은 채팅 본문에서 제거되고 이미지가 표시돼. '
    '최대 4개, 파일당 4 MiB야. '
)

WORK_TOOLS_PROMPT = (
    '이전 안내의 작업 도구 제한을 해제하고, Relay 서버 선택은 메시지 송수신에만 적용해. '
    '실제 작업에는 이 세션에서 제공하는 이미지 생성·편집, 파일 읽기·쓰기·편집, '
    '터미널 실행, 웹 검색, 브라우저, 연결된 다른 MCP 등 필요한 도구를 기존 권한과 사용자가 지시한 작업 범위 안에서 사용할 수 있어. '
    'Relay 외 도구가 필요하다는 이유만으로 작업을 보류하지 마. '
    '이미지 생성 요청은 실제 이미지 생성 도구로 수행하고, 그 도구가 없거나 실행에 실패하면 정확한 사유를 알려줘. '
)

WORK_TOOLS_INSTRUCTIONS = (
    'Relay is the messaging transport, not a restriction on task tools. '
    'For authorized work, use the image generation/editing, file, terminal, web, browser, '
    'and other MCP tools actually available in this session within existing permissions. '
    'Do not defer work merely because it needs a non-Relay tool. '
    'Do not claim an image was generated if no image-generation tool is available or the call failed. '
)
