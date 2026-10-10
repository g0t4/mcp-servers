import base64
import json
import mimetypes
import urllib.error
import urllib.request
from pathlib import Path


# LocateAnything-3B is served remotely by llama-server on build21.lan.
# Requires the yuuko-eth llama.cpp fork (mtmd-grounders branch) and `--special`
# so the <ref>/<box> grounding control tokens survive in the response.
LOCATE_SERVER_URL = "http://build21.lan:8030/v1/chat/completions"


def _image_data_url(image_path: str) -> str:
    """Encode an image file as a base64 data URL for the OpenAI image_url field."""
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"image not found: {path}")
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def locate_anything_infer(image_path: str, question: str) -> str:
    """Ask the remote LocateAnything-3B server to ground `question` in `image_path`.

    Returns the raw model output text, including <ref> and <box> control tokens
    (coordinates are normalized to 0-1000).
    """
    payload = {
        "model": "locateanything-3b",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _image_data_url(image_path)}},
                    {"type": "text", "text": question},
                ],
            }
        ],
        "temperature": 0.7,
        "top_p": 0.9,
    }
    request = urllib.request.Request(
        LOCATE_SERVER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        return f"Error calling locate_anything server ({LOCATE_SERVER_URL}): {error}"
    except TimeoutError as error:
        return f"Error calling locate_anything server: timed out: {error}"

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return f"Error: unexpected response from locate_anything server: {body}"
