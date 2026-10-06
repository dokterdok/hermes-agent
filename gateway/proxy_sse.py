"""Decode proxy response lines without splitting UTF-8 characters at network boundaries."""
import codecs
import json


def proxy_sse_data(line):
    """Return the completion marker and optional text delta; malformed frames stay inert."""
    line = line.strip()
    if not line.startswith("data:"):
        return False, ""
    data = line[5:].removeprefix(" ")
    if data.strip() == "[DONE]":
        return True, ""
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return False, ""
    choices = event.get("choices") if isinstance(event, dict) else None
    if not isinstance(choices, list) or not choices:
        return False, ""
    delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
    content = delta.get("content") if isinstance(delta, dict) else None
    return False, content if isinstance(content, str) else ""


class ProxySSEBuffer:
    def __init__(self):
        self.text = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def feed(self, chunk):
        self.text += self._decoder.decode(chunk)
        while "\n" in self.text:
            line, self.text = self.text.split("\n", 1)
            yield line

    def finish(self):
        """Flush an unterminated final frame only after the peer closes the stream."""
        residual = self.text + self._decoder.decode(b"", final=True)
        self.text = ""
        return residual
