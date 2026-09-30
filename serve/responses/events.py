"""One state machine produces both the final object and named SSE events."""
from copy import deepcopy
import json
import time
import uuid


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex


def response_object(req, definitions):
    result = {"id": uid("resp"), "object": "response", "created_at": time.time(),
            "status": "queued" if req.get("background") else "in_progress", "background": bool(req.get("background")),
            "error": None, "incomplete_details": None, "instructions": req.get("instructions"),
            "model": req["model"], "output": [], "parallel_tool_calls": req.get("parallel_tool_calls", True),
            "previous_response_id": req.get("previous_response_id"), "reasoning": req.get("reasoning") or {},
            "store": req.get("store", True), "text": {"format": {"type": "text"}, **(req.get("text") or {})},
            "tool_choice": req.get("tool_choice", "auto"), "tools": definitions,
            "max_output_tokens": req.get("max_output_tokens"), "temperature": req.get("temperature", 0),
            "top_p": req.get("top_p", 1), "truncation": "disabled", "metadata": req.get("metadata") or {},
            "usage": None, "service_tier": "default"}
    for field in ("prompt_cache_key", "safety_identifier", "user"):
        if field in req:
            result[field] = req[field]
    return result


class Output:
    def __init__(self, record, tools, encrypt):
        self.record, self.tools, self.encrypt = record, tools, encrypt
        self.sequence, self.current, self.fragments = 0, None, []
        self.reasoning_text = ""
        self.summary = None

    def event(self, kind, **values):
        result = {"type": kind, "sequence_number": self.sequence, **deepcopy(values)}
        self.sequence += 1
        return result

    def response_event(self, kind):
        return self.event(kind, response=self.record.snapshot())

    def index(self):
        return len(self.record.response["output"]) - 1

    def close(self, status="completed"):
        item = self.current
        if item is None:
            return
        txt = "".join(self.fragments)
        i, base = self.index(), {"item_id": item["id"], "output_index": self.index(), "content_index": 0}
        if item["type"] == "reasoning":
            item["content"][0]["text"] = txt
            self.reasoning_text += txt
            item["encrypted_content"] = self.encrypt(txt)
            yield self.event("response.reasoning_text.done", **base, text=txt)
            if self.summary:
                summary = {"type": "summary_text", "text": ""}
                yield self.event("response.reasoning_summary_part.added", item_id=item["id"], output_index=i,
                                 summary_index=0, part=summary)
                yield self.event("response.reasoning_summary_text.delta", item_id=item["id"], output_index=i,
                                 summary_index=0, delta=self.summary)
                summary["text"] = self.summary
                item["summary"] = [summary]
                yield self.event("response.reasoning_summary_text.done", item_id=item["id"], output_index=i,
                                 summary_index=0, text=self.summary)
                yield self.event("response.reasoning_summary_part.done", item_id=item["id"], output_index=i,
                                 summary_index=0, part=summary)
        else:
            item["content"][0]["text"] = txt
            yield self.event("response.output_text.done", **base, text=txt, logprobs=[])
        yield self.event("response.content_part.done", **base, part=item["content"][0])
        item["status"] = status
        yield self.event("response.output_item.done", output_index=i, item=item)
        self.current, self.fragments = None, []

    def accept(self, ev):
        if ev.kind in ("reasoning", "content"):
            if not ev.text:
                return
            typ = "reasoning" if ev.kind == "reasoning" else "message"
            if self.current is not None and self.current["type"] != typ:
                yield from self.close()
            if self.current is None:
                item = {"id": uid("rs" if typ == "reasoning" else "msg"), "type": typ, "status": "in_progress"}
                part = {"type": "reasoning_text" if typ == "reasoning" else "output_text", "text": ""}
                if typ == "reasoning":
                    item["summary"] = []
                else:
                    item["role"] = "assistant"
                    part.update(annotations=[], logprobs=[])
                item["content"] = [part]
                self.current = item
                self.record.response["output"].append(item)
                yield self.event("response.output_item.added", output_index=self.index(), item=item)
                yield self.event("response.content_part.added", output_index=self.index(), item_id=item["id"],
                                 content_index=0, part=part)
            self.fragments.append(ev.text)
            values = {"output_index": self.index(), "item_id": self.current["id"], "content_index": 0, "delta": ev.text}
            if typ == "message":
                values["logprobs"] = []
            yield self.event("response.reasoning_text.delta" if typ == "reasoning" else "response.output_text.delta", **values)
        elif ev.kind == "tool_call":
            yield from self.close()
            call = ev.call
            entry = self.tools.by_name[call.name]
            if entry["kind"] == "tool_search":
                item = {"id": uid("ts"), "type": "tool_search_call", "call_id": call.id, "execution": "client",
                        "status": "in_progress", "arguments": call.arguments}
                self.record.response["output"].append(item)
                yield self.event("response.output_item.added", output_index=self.index(), item=item)
                item["status"] = "completed"
                yield self.event("response.output_item.done", output_index=self.index(), item=item)
                return
            custom = entry["kind"] == "custom"
            item = {"id": uid("ctc" if custom else "fc"), "type": "custom_tool_call" if custom else "function_call",
                    "call_id": call.id, "name": entry["name"]}
            if entry["namespace"]:
                item["namespace"] = entry["namespace"]
            field = "input" if custom else "arguments"
            value = call.arguments["input"] if custom else json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":"))
            item[field] = ""
            if not custom:
                item["status"] = "in_progress"
            self.record.response["output"].append(item)
            i, prefix = self.index(), "response.custom_tool_call_input" if custom else "response.function_call_arguments"
            yield self.event("response.output_item.added", output_index=i, item=item)
            yield self.event(prefix + ".delta", output_index=i, item_id=item["id"], delta=value)
            item[field] = value
            extra = {} if custom else {"name": item["name"]}
            yield self.event(prefix + ".done", output_index=i, item_id=item["id"], **{field: value}, **extra)
            if not custom:
                item["status"] = "completed"
            yield self.event("response.output_item.done", output_index=i, item=item)
