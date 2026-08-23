from mcp.types import Tool, TextContent, ErrorData
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS

LOCATE_ANYTHING_TOOL_NAME = "locate_anything"

LOCATE_ANYTHING_TOOL = Tool(
    name=LOCATE_ANYTHING_TOOL_NAME,
    description="Answer visual questions about a picture and return bounding boxes using the LocateAnything-3B model.",
    inputSchema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The visual question to ask about the image."
            },
            "image_path": {
                "type": "string",
                "description": "The path to the image file."
            }
        },
        "required": ["question", "image_path"]
    }
)


def locate_anything(question: str, image_path: str) -> str:
    from subagents.locate.worker import locate_anything_infer
    return locate_anything_infer(image_path, question)
