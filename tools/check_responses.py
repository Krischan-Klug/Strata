"""Real-engine native Responses smoke test. Executes no returned tool code."""
import argparse
import base64
from io import BytesIO
import json
import os
import time
import urllib.request

from openai import OpenAI
from openai.types.responses import Response, ResponseStreamEvent, CompactedResponse
from PIL import Image
from pydantic import TypeAdapter


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    p.add_argument("--api-key", default=os.environ.get("STRATA_API_KEY", "local"))
    p.add_argument("--timeout", type=float, default=180)
    args = p.parse_args()
    client = OpenAI(base_url=args.base_url.rstrip("/"), api_key=args.api_key, timeout=args.timeout, max_retries=0)
    model = client.models.list().data[0].id
    event_type = TypeAdapter(ResponseStreamEvent)
    results = []

    def create(**kw):
        result = client.responses.create(**({"model": model, "input": "Reply exactly READY.",
                    "reasoning": {"effort": "none"}, "max_output_tokens": 512, "temperature": 0} | kw))
        if not kw.get("stream"):
            Response.model_validate(result.model_dump(by_alias=True))
            assert result.status == "completed", result.model_dump()
        return result

    def check(name, fn):
        started = time.monotonic()
        fn()
        result = {"check": name, "ok": True, "seconds": round(time.monotonic() - started, 2)}
        results.append(result)
        print(json.dumps(result), flush=True)

    def text():
        assert create(max_output_tokens=32).output_text.strip() == "READY"

    def stream():
        events = [e.model_dump(exclude_none=True) for e in create(stream=True, max_output_tokens=32)]
        for e in events:
            event_type.validate_python(e)
        assert events[-1]["type"] == "response.completed", events[-1]
        assert [e["sequence_number"] for e in events] == list(range(len(events)))
        assert "".join(e["delta"] for e in events if e["type"] == "response.output_text.delta").strip() == "READY"

    def structured():
        fmt = {"type": "json_schema", "name": "ready", "strict": True, "schema": {"type": "object",
               "properties": {"ready": {"type": "boolean", "const": True}}, "required": ["ready"],
               "additionalProperties": False}}
        result = create(input='Return JSON with ready=true.', text={"format": fmt}, max_output_tokens=64)
        assert json.loads(result.output_text) == {"ready": True}

    def function():
        tool = {"type": "function", "name": "weather", "description": "Get temperature for a city", "strict": True,
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                               "required": ["city"], "additionalProperties": False}}
        result = create(input="Call weather for Berlin.", tools=[tool], tool_choice={"type": "function", "name": "weather"})
        fn = next(item for item in result.output if item.type == "function_call")
        assert json.loads(fn.arguments)["city"].lower() == "berlin"
        result = create(input=[{"type": "function_call_output", "call_id": fn.call_id, "output": "Berlin: 20 C"}],
                        previous_response_id=result.id, tools=[tool], instructions="Answer the user using the tool result.")
        assert "20" in result.output_text

    def custom():
        tool = {"type": "custom", "name": "exec", "description": "Receive literal Python code; use print(1)",
                "format": {"type": "grammar", "syntax": "regex", "definition": r"print\(1\)"}}
        result = create(input="Call exec with exactly print(1).", tools=[tool], tool_choice={"type": "custom", "name": "exec"})
        fn = next(item for item in result.output if item.type == "custom_tool_call")
        assert fn.input == "print(1)"
        result = create(input=[item.model_dump(exclude_none=True) for item in result.output] +
                       [{"type": "custom_tool_call_output", "call_id": fn.call_id, "output": "1"}], tools=[tool],
                       instructions="The tool has returned. Respond exactly DONE; do not call any tool again.")
        assert result.output_text.strip() == "DONE"

    def store():
        result = create(max_output_tokens=32)
        assert client.responses.retrieve(result.id).output_text.strip() == "READY"
        assert client.responses.input_items.list(result.id).data
        client.responses.delete(result.id)

    def compact():
        result = client.responses.compact(model=model, input=[{"role": "user", "content": "Remember the secret word BLUEBIRD."},
                                         {"role": "assistant", "content": "I will remember BLUEBIRD."}])
        CompactedResponse.model_validate(result.model_dump(by_alias=True))
        result = create(input=[item.model_dump(exclude_none=True) for item in result.output] +
                              [{"role": "user", "content": "What word did I ask you to remember? Reply with only the word."}])
        assert "BLUEBIRD" in result.output_text

    def vision():
        request = urllib.request.Request(args.base_url.rsplit("/v1", 1)[0] + "/health",
                                         headers={"Authorization": "Bearer " + args.api_key})
        info = json.loads(urllib.request.urlopen(request, timeout=10).read())
        if not info.get("images"):
            print(json.dumps({"check": "image", "skipped": "vision not loaded"}), flush=True)
            return
        image = Image.new("RGB", (64, 64), (255, 0, 0))
        data = BytesIO()
        image.save(data, format="PNG")
        url = "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()
        result = create(input=[{"role": "user", "content": [{"type": "input_text", "text": "What color is this? Reply one word."},
                              {"type": "input_image", "image_url": url}]}], max_output_tokens=32)
        assert "red" in result.output_text.lower(), result.output_text

    try:
        for name, fn in [("text", text), ("SSE", stream), ("JSON schema", structured), ("function round trip", function),
                         ("custom round trip", custom), ("store", store), ("compaction", compact), ("image", vision)]:
            check(name, fn)
        print(json.dumps({"ok": True, "model": model, "checks": len(results)}), flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
