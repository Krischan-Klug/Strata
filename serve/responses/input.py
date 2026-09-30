"""Responses wire items -> the existing model template's message/tool form."""
from copy import deepcopy
import json
import re

from serve.frontend import effort_kwargs, openai_to_messages
from .constraints import Grammar, loads, schema_validator
from .errors import APIError, unsupported

NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SEARCH_NAME = "__strata_tool_search"


def name(value, param):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise APIError("tool names must contain 1-64 letters, digits, underscores or hyphens", param)
    return value


def array(value, param):
    if not isinstance(value, list):
        raise APIError("expected an array", param)
    return value


def text(value, param):
    if not isinstance(value, str):
        raise APIError("expected a string", param)
    return value


def parts(value, param, output=False):
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    result = []
    for i, part in enumerate(array(value, param)):
        p = f"{param}[{i}]"
        if not isinstance(part, dict):
            raise APIError("content parts must be objects", p)
        kind = part.get("type")
        if kind in ("input_text", "output_text", "text"):
            result.append({"type": "text", "text": text(part.get("text"), p + ".text")})
        elif kind in ("input_image", "image_url"):
            src = part.get("image_url")
            if isinstance(src, dict):
                src = src.get("url")
            if not isinstance(src, str) or not src.startswith(("https://", "http://", "data:image/")):
                raise APIError("image_url must be an HTTP(S) or image data URL", p + ".image_url")
            detail = part.get("detail", "auto")
            if detail not in ("auto", "low", "high", "original"):
                raise APIError("invalid image detail", p + ".detail")
            result.append({"type": "image_url", "image_url": {"url": src}})
        elif kind == "refusal" and output:
            result.append({"type": "text", "text": text(part.get("refusal"), p + ".refusal")})
        else:
            unsupported(p + ".type:" + str(kind))
    return result


def items(value):
    if isinstance(value, str):
        return [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": value}]}]
    result = deepcopy(array(value, "input"))
    for i, item in enumerate(result):
        if not isinstance(item, dict):
            raise APIError("input items must be objects", f"input[{i}]")
        item.setdefault("type", "message")
    return result


class Tools:
    def __init__(self, definitions):
        self.definitions, self.by_name, self.scopes, self.loaded = deepcopy(definitions), {}, {}, set()
        for i, tool in enumerate(array(definitions, "tools")):
            self.add(tool, f"tools[{i}]")

    def add(self, tool, param, namespace=None):
        if not isinstance(tool, dict):
            raise APIError("tool must be an object", param)
        kind = tool.get("type")
        if kind == "tool_search":
            if namespace or SEARCH_NAME in self.by_name:
                raise APIError("at most one top-level tool_search is supported", param)
            if tool.get("execution", "server") != "client":
                unsupported(param + ".execution:server")
            schema = tool.get("parameters") or {"type": "object", "properties": {"query": {"type": "string"}},
                                               "required": ["query"], "additionalProperties": False}
            self.by_name[SEARCH_NAME] = {"name": SEARCH_NAME, "namespace": None, "kind": "tool_search", "strict": True,
                "grammar": None, "validator": schema_validator(schema, param + ".parameters"), "definition": deepcopy(tool),
                "template": {"name": SEARCH_NAME, "description": tool.get("description") or "Search for more tools",
                             "parameters": schema}}
            return
        n = name(tool.get("name"), param + ".name") if kind in ("namespace", "function", "custom") else None
        if kind == "namespace":
            if namespace:
                raise APIError("nested tool namespaces are not supported", param)
            desc = text(tool.get("description", ""), param + ".description")
            if n in self.scopes and self.scopes[n] != desc:
                raise APIError("conflicting namespace descriptions", param)
            self.scopes[n] = desc
            for i, child in enumerate(array(tool.get("tools"), param + ".tools")):
                self.add(child, f"{param}.tools[{i}]", n)
            return
        if kind not in ("function", "custom"):
            unsupported(param + ".type:" + str(kind))
        internal = f"{namespace}.{n}" if namespace else n
        if internal == SEARCH_NAME:
            raise APIError("this tool name is reserved", param + ".name")
        if internal in self.by_name:
            old = {k: v for k, v in self.by_name[internal]["definition"].items() if k != "defer_loading"}
            new = {k: v for k, v in tool.items() if k != "defer_loading"}
            if old == new:
                return  # an identical additional_tools declaration may be replayed
            raise APIError("conflicting tool identity: " + internal, param)
        desc = text(tool.get("description") or "", param + ".description")
        strict = tool.get("strict")
        if strict is not None and not isinstance(strict, bool):
            raise APIError("strict must be a boolean", param + ".strict")
        if tool.get("defer_loading") is not None and not isinstance(tool["defer_loading"], bool):
            raise APIError("defer_loading must be a boolean", param + ".defer_loading")
        for field in ("allowed_callers", "output_schema"):
            if tool.get(field):
                unsupported(param + "." + field)
        entry = {"name": n, "namespace": namespace, "kind": kind, "strict": strict is not False,
                 "grammar": None, "validator": None, "definition": deepcopy(tool)}
        if kind == "function":
            schema = tool.get("parameters") or {"type": "object", "properties": {}, "additionalProperties": False}
            entry["validator"] = schema_validator(schema, param + ".parameters")
        else:
            fmt = tool.get("format") or {"type": "text"}
            if not isinstance(fmt, dict):
                raise APIError("custom format must be an object", param + ".format")
            if fmt.get("type") == "grammar":
                entry["grammar"] = Grammar(fmt, param + ".format")
                desc += "\nThe input must follow this " + fmt["syntax"] + " grammar:\n" + fmt["definition"]
            elif fmt.get("type") != "text":
                unsupported(param + ".format.type")
            schema = {"type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"],
                      "additionalProperties": False}
            desc += "\nPass the complete raw custom tool input as the string parameter `input`."
        entry["template"] = {"name": internal, "description": desc, "parameters": schema}
        self.by_name[internal] = entry

    def identity(self, item, param):
        if not isinstance(item, dict):
            raise APIError("tool identity must be an object", param)
        n = name(item.get("name"), param + ".name")
        ns = item.get("namespace")
        if ns is not None:
            name(ns, param + ".namespace")
        return f"{ns}.{n}" if ns else n

    def selected(self, choice):
        if choice in (None, "auto", "required"):
            if choice == "required" and not self.by_name:
                raise APIError("tool_choice required needs at least one tool", "tool_choice")
            return set(self.by_name), choice == "required"
        if choice == "none":
            return set(), False
        if not isinstance(choice, dict):
            raise APIError("invalid tool_choice", "tool_choice")
        if choice.get("type") in ("function", "custom"):
            key = self.identity(choice, "tool_choice")
            if key not in self.by_name or self.by_name[key]["kind"] != choice["type"]:
                raise APIError("tool_choice refers to an undeclared tool", "tool_choice")
            return {key}, True
        if choice.get("type") == "allowed_tools":
            mode = choice.get("mode", "auto")
            if mode not in ("auto", "required"):
                raise APIError("invalid allowed_tools mode", "tool_choice.mode")
            selected = set()
            for i, item in enumerate(array(choice.get("tools"), "tool_choice.tools")):
                key = self.identity(item, f"tool_choice.tools[{i}]")
                if key not in self.by_name:
                    raise APIError("allowed_tools refers to an undeclared tool", "tool_choice.tools")
                selected.add(key)
            if not selected and mode == "required":
                raise APIError("required allowed_tools must not be empty", "tool_choice.tools")
            return selected, mode == "required"
        unsupported("tool_choice.type")


def normalize(history, instructions, tools, decrypt):
    messages, calls, pending, reasoning = [], {}, {}, ""
    if instructions:
        messages.append({"role": "system", "content": instructions})
    for i, item in enumerate(history):
        p, kind = f"input[{i}]", item.get("type", "message")
        if kind == "additional_tools":
            continue  # collected once, at namespace scope, before rendering
        if kind == "compaction":
            summary = decrypt(item.get("encrypted_content"), p + ".encrypted_content", expected="compaction")
            messages.append({"role": "developer", "content": "Previous conversation state:\n" + summary})
            continue
        if kind == "reasoning":
            encrypted = item.get("encrypted_content")
            if encrypted:
                reasoning += decrypt(encrypted, p + ".encrypted_content")
            else:
                for part in item.get("content") or []:
                    if part.get("type") == "reasoning_text":
                        reasoning += text(part.get("text"), p + ".content.text")
            continue
        if kind == "tool_search_call":
            if item.get("execution") != "client":
                unsupported(p + ".execution")
            call_id = text(item.get("call_id"), p + ".call_id")
            if not call_id or call_id in calls:
                raise APIError("call_id must be unique and nonempty", p + ".call_id")
            if not isinstance(item.get("arguments"), dict):
                raise APIError("tool search arguments must be an object", p + ".arguments")
            messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": call_id,
                "type": "function", "function": {"name": SEARCH_NAME, "arguments": item["arguments"]}}]})
            calls[call_id], pending[call_id] = "tool_search_call", SEARCH_NAME
            continue
        if kind == "tool_search_output":
            call_id = text(item.get("call_id"), p + ".call_id")
            if item.get("execution") != "client" or call_id not in pending or calls[call_id] != "tool_search_call":
                raise APIError("tool search output needs a matching client search call", p)
            identities = []
            for t in array(item.get("tools"), p + ".tools"):
                if t.get("type") == "namespace":
                    identities.extend(t["name"] + "." + child["name"] for child in t["tools"])
                else:
                    identities.append(t["name"])
            tools.loaded.update(identities)
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": "Available tools: " + json.dumps(identities)})
            del pending[call_id]
            continue
        if kind in ("function_call", "custom_tool_call"):
            call_id = text(item.get("call_id"), p + ".call_id")
            if not call_id or call_id in calls:
                raise APIError("call_id must be unique and nonempty", p + ".call_id")
            key = tools.identity(item, p)
            entry = tools.by_name.get(key)
            if entry and entry["kind"] != ("function" if kind == "function_call" else "custom"):
                raise APIError("tool call type does not match its declaration", p)
            if kind == "function_call":
                try:
                    arguments = loads(text(item.get("arguments"), p + ".arguments"))
                except (ValueError, TypeError) as e:
                    raise APIError("tool arguments must be a JSON object", p + ".arguments") from e
                if not isinstance(arguments, dict):
                    raise APIError("tool arguments must be a JSON object", p + ".arguments")
            else:
                arguments = {"input": text(item.get("input"), p + ".input")}
            call = {"id": call_id, "type": "function", "function": {"name": key, "arguments": arguments}}
            if messages and messages[-1]["role"] == "assistant" and messages[-1].get("tool_calls"):
                messages[-1]["tool_calls"].append(call)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
            if reasoning:
                messages[-1]["reasoning_content"] = reasoning
                reasoning = ""
            calls[call_id], pending[call_id] = kind, key
            continue
        if kind in ("function_call_output", "custom_tool_call_output"):
            call_id = text(item.get("call_id"), p + ".call_id")
            expected = "function_call" if kind == "function_call_output" else "custom_tool_call"
            if call_id not in pending or calls[call_id] != expected:
                raise APIError("tool output needs a matching, unanswered call_id", p + ".call_id")
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": parts(item.get("output"), p + ".output", output=True)})
            del pending[call_id]
            continue
        if kind == "message":
            role = item.get("role")
            if role not in ("user", "assistant", "developer", "system"):
                raise APIError("invalid message role", p + ".role")
            if pending and role != "assistant":
                raise APIError("all tool calls must have outputs before the next message", p)
            m = {"role": role, "content": parts(item.get("content"), p + ".content", output=role == "assistant")}
            if role == "assistant" and reasoning:
                m["reasoning_content"], reasoning = reasoning, ""
            messages.append(m)
            continue
        unsupported(p + ".type:" + str(kind))
    if pending:
        raise APIError("missing outputs for tool calls: " + ", ".join(pending), "input")
    return messages


def prepare_request(req, model, history, decrypt):
    if not isinstance(req, dict):
        raise APIError("request must be an object")
    supported = {"model", "input", "instructions", "tools", "tool_choice", "parallel_tool_calls", "reasoning",
                 "text", "max_output_tokens", "temperature", "top_p", "store", "stream", "background", "metadata",
                 "previous_response_id", "include", "prompt_cache_key", "safety_identifier", "user", "client_metadata",
                 "truncation", "service_tier", "stream_options"}
    for k in req:
        if k not in supported:
            unsupported(k)
    if req.get("model") != model:
        raise APIError("model not found: " + str(req.get("model")), "model", "model_not_found", 404)
    for k in ("store", "stream", "background", "parallel_tool_calls"):
        if req.get(k) is not None and not isinstance(req[k], bool):
            raise APIError("expected a boolean", k)
    if req.get("background") and req.get("store") is False:
        raise APIError("background requires store=true", "background")
    if req.get("truncation", "disabled") != "disabled":
        unsupported("truncation")
    if req.get("service_tier", "auto") not in ("auto", "default"):
        unsupported("service_tier")
    for k in ("temperature", "top_p"):
        v = req.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (float, int)) or not 0 <= v <= (2 if k == "temperature" else 1)):
            raise APIError("invalid sampling value", k)
    budget = req.get("max_output_tokens")
    if budget is not None and (isinstance(budget, bool) or not isinstance(budget, int) or budget < 1):
        raise APIError("max_output_tokens must be a positive integer", "max_output_tokens")
    instructions = req.get("instructions")
    if instructions is not None:
        text(instructions, "instructions")
    metadata = req.get("metadata") or {}
    if not isinstance(metadata, dict) or len(metadata) > 16 or any(not isinstance(k, str) or len(k) > 64 or
            not isinstance(v, str) or len(v) > 512 for k, v in metadata.items()):
        raise APIError("metadata allows at most 16 string pairs (keys 64, values 512 characters)", "metadata")
    for field in ("prompt_cache_key", "safety_identifier", "user"):
        if req.get(field) is not None:
            text(req[field], field)
    include = req.get("include") or []
    for value in array(include, "include"):
        if value not in ("reasoning.encrypted_content", "message.input_image.image_url"):
            unsupported("include:" + str(value))
    if req.get("stream_options"):
        options = req["stream_options"]
        if not isinstance(options, dict) or any(k != "include_obfuscation" for k in options):
            unsupported("stream_options")
        if options.get("include_obfuscation") not in (None, False):
            unsupported("stream_options.include_obfuscation")
    definitions = deepcopy(req.get("tools") or [])
    array(definitions, "tools")
    for i, item in enumerate(history):
        if item.get("type") == "additional_tools":
            if item.get("role") != "developer":
                raise APIError("additional_tools requires developer role", f"input[{i}].role")
            definitions.extend(array(item.get("tools"), f"input[{i}].tools"))
        elif item.get("type") == "tool_search_output":
            definitions.extend(array(item.get("tools"), f"input[{i}].tools"))
    tools = Tools(definitions)
    selected, required = tools.selected(req.get("tool_choice", "auto"))
    messages = normalize(history, instructions, tools, decrypt)
    deferred = {k for k, entry in tools.by_name.items() if entry["definition"].get("defer_loading")}
    if deferred and SEARCH_NAME not in tools.by_name:
        raise APIError("deferred tools require a client tool_search declaration", "tools")
    selected -= deferred - tools.loaded
    scopes = [f"Tool namespace {n}:\n{desc}" for n, desc in tools.scopes.items() if desc]
    if scopes:
        messages.insert(0, {"role": "system", "content": "\n\n".join(scopes)})
    if required:
        messages.insert(0, {"role": "system", "content": "You must respond with a call to an available tool."})
    if req.get("parallel_tool_calls") is False:
        messages.insert(0, {"role": "system", "content": "Call at most one tool in this response."})
    config = req.get("text") or {}
    if not isinstance(config, dict):
        raise APIError("text must be an object", "text")
    for k in config:
        if k not in ("format", "verbosity"):
            unsupported("text." + k)
    verbosity = config.get("verbosity")
    if verbosity is not None:
        if verbosity not in ("low", "medium", "high"):
            raise APIError("invalid verbosity", "text.verbosity")
        messages.insert(0, {"role": "system", "content": "Answer with " + verbosity + " verbosity."})
    fmt = config.get("format") or {"type": "text"}
    if not isinstance(fmt, dict):
        raise APIError("text.format must be an object", "text.format")
    validator = None
    if fmt.get("type") == "json_schema":
        name(fmt.get("name"), "text.format.name")
        validator = schema_validator(fmt.get("schema"), "text.format.schema")
        messages.insert(0, {"role": "system", "content": "Return only JSON conforming to this schema:\n" +
                            json.dumps(fmt["schema"], ensure_ascii=False)})
    elif fmt.get("type") == "json_object":
        messages.insert(0, {"role": "system", "content": "Return only a valid JSON object."})
    elif fmt.get("type") != "text":
        unsupported("text.format.type")
    reasoning = req.get("reasoning") or {}
    if not isinstance(reasoning, dict):
        raise APIError("reasoning must be an object", "reasoning")
    for k in reasoning:
        if k not in ("effort", "summary", "context"):
            unsupported("reasoning." + k)
    if reasoning.get("summary") not in (None, "auto", "concise", "detailed"):
        raise APIError("invalid reasoning.summary", "reasoning.summary")
    kw = effort_kwargs(reasoning.get("effort"))
    if reasoning.get("context") is not None:
        if reasoning["context"] not in ("all_turns", "last_turn"):
            raise APIError("reasoning.context must be all_turns or last_turn", "reasoning.context")
        kw["preserve_thinking"] = reasoning["context"] == "all_turns"
    messages, _, _ = openai_to_messages({"messages": messages})
    if not any(m["role"] == "user" for m in messages):
        # Responses permits assistant/tool-only replay and instruction-only input.
        # Qwen's model-pack template requires a user boundary. An empty boundary
        # supplies no new user instructions and does not change the API transcript.
        at = next((i for i, m in enumerate(messages) if m["role"] != "system"), len(messages))
        messages.insert(at, {"role": "user", "content": ""})
    template_tools = [entry["template"] for key, entry in tools.by_name.items() if key in selected] or None
    return {"messages": messages, "tools": tools, "selected": selected, "required": required, "kw": kw,
            "template_tools": template_tools, "format": fmt, "validator": validator}
