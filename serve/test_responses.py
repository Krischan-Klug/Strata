"""Native Responses contract tests: actual HTTP + strict official SDK models.

Install serve/responses/requirements-test.txt; run python -m unittest serve.test_responses.
No GPU, weights, internet or paid API key required.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from openai import OpenAI, BadRequestError, NotFoundError
from openai.types.responses import Response, ResponseStreamEvent
from pydantic import TypeAdapter

from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, MockEngine, Service, serve
from serve.responses.constraints import Grammar
from serve.responses.errors import APIError
from serve.responses.store import Store

ROOT = Path(__file__).resolve().parents[1]
EVENT = TypeAdapter(ResponseStreamEvent)
FUNCTION = {"type": "function", "name": "lookup", "description": "Look up a city", "strict": True,
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"],
                           "additionalProperties": False}}


def call(tool="lookup", parameter="city", value="Berlin"):
    return f"<tool_call><function={tool}><parameter={parameter}>{value}</parameter></function></tool_call>"


class NativeResponses(unittest.TestCase):
    def start(self, script="READY", context=32768, delay=0, store=None):
        tok = ByteTokenizer()
        self.engine = MockEngine(tok, script, max_context=context, delay_s=delay)
        self.svc = Service(self.engine, tok, ChatTemplate(ROOT / "serve/chat_template.jinja"), model_name="test-model")
        self.http = serve(self.svc, port=0)
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.addCleanup(self.svc.responses.store.close)
        if store:
            self.svc.responses.store = store
        self.base = f"http://127.0.0.1:{self.http.server_address[1]}"
        self.client = OpenAI(base_url=self.base + "/v1", api_key="test", max_retries=0, timeout=10)
        self.addCleanup(self.client.close)

    def create(self, **kwargs):
        defaults = {"model": "test-model", "input": "hi", "reasoning": {"effort": "none"}, "max_output_tokens": 1024}
        return self.client.responses.create(**(defaults | kwargs))

    def wire(self, path, body=None, method=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def events(self, **kwargs):
        result = [ev.model_dump(exclude_none=True) for ev in self.create(stream=True, **kwargs)]
        for ev in result:
            EVENT.validate_python(ev)
        self.assertEqual([e["sequence_number"] for e in result], list(range(len(result))))
        return result

    def test_sdk_create_retrieve_delete_and_unstored(self):
        self.start()
        result = self.create(metadata={"test": "native"})
        Response.model_validate(result.model_dump())
        self.assertEqual(result.output_text, "READY")
        self.assertEqual(result.usage.total_tokens, result.usage.input_tokens + result.usage.output_tokens)
        self.assertEqual(self.client.responses.retrieve(result.id).output_text, "READY")
        self.client.responses.delete(result.id)
        with self.assertRaises(NotFoundError):
            self.client.responses.retrieve(result.id)
        result = self.create(store=False)
        with self.assertRaises(NotFoundError):
            self.client.responses.retrieve(result.id)

    def test_text_stream_has_complete_matching_items(self):
        self.start("Grüße 👋")
        events = self.events()
        self.assertEqual(events[0]["type"], "response.created")
        self.assertEqual(events[-1]["type"], "response.completed")
        self.assertEqual("".join(e["delta"] for e in events if e["type"] == "response.output_text.delta"), "Grüße 👋")
        added = next(e["item"] for e in events if e["type"] == "response.output_item.added")
        closed = next(e["item"] for e in events if e["type"] == "response.output_item.done")
        self.assertEqual(added["id"], closed["id"])
        self.assertEqual(closed, events[-1]["response"]["output"][0])

    def test_sdk_stream_handles_engine_heartbeats(self):
        self.start()
        original = self.engine.generate
        def heartbeat(*args, **kwargs):
            yield None
            yield from original(*args, **kwargs)
        self.engine.generate = heartbeat
        self.assertEqual(self.events()[-1]["type"], "response.completed")

    def test_multimodal_tool_result_uses_vision_and_cleans_tempfile(self):
        from serve.test_server import ImageMarkers
        self.start([call(), "RED"])
        with tempfile.TemporaryDirectory() as d:
            self.svc.vision = ImageMarkers.FakeVision(d)
            first = self.create(tools=[FUNCTION])
            result = self.create(previous_response_id=first.id, tools=[FUNCTION], input=[{
                "type": "function_call_output", "call_id": first.output[0].call_id, "output": [
                    {"type": "input_text", "text": "See this image"},
                    {"type": "input_image", "image_url": "data:image/png;base64,YQ=="}]}])
            self.assertEqual(result.output_text, "RED")
            self.assertIsNotNone(self.engine.last_embeddings)
            self.assertFalse(Path(self.engine.last_embeddings).exists())
            status, body = self.wire("/v1/responses/input_tokens", {"model": "test-model", "input": [
                {"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,YQ=="}]}]})
            self.assertEqual(status, 200, body)
            self.assertFalse(list(Path(d).glob("req-*.sve")))

    def test_specific_function_choice_and_allowed_tools(self):
        self.start(call())
        for choice in [{"type": "function", "name": "lookup"}, {"type": "allowed_tools", "mode": "required",
                       "tools": [{"type": "function", "name": "lookup"}]}]:
            result = self.create(tools=[FUNCTION], tool_choice=choice)
            self.assertEqual(result.status, "completed")

    def test_codex_apply_patch_grammar_with_blank_lines(self):
        # Minimal fixture of Codex 0.159.2's actual freeform patch grammar.
        grammar = '''start: begin_patch hunk+ end_patch
begin_patch: "*** Begin Patch" LF
end_patch: "*** End Patch" LF?
hunk: add_hunk | delete_hunk | update_hunk
add_hunk: "*** Add File: " filename LF add_line+
delete_hunk: "*** Delete File: " filename LF
update_hunk: "*** Update File: " filename LF change_move? change?
filename: /(.+)/
add_line: "+" /(.*)/ LF -> line
change_move: "*** Move to: " filename LF
change: (change_context | change_line)+ eof_line?
change_context: ("@@" | "@@ " /(.+)/) LF
change_line: ("+" | "-" | " ") /(.*)/ LF
eof_line: "*** End of File" LF
%import common.LF
'''
        parsed = Grammar({"syntax": "lark", "definition": grammar}, "format")
        parsed.validate("*** Begin Patch\n*** Add File: hello.py\n+print(1)\n+\n*** End Patch\n")
        with self.assertRaises(APIError):
            parsed.validate("print(1)")

    def test_long_conversation_keeps_every_message(self):
        self.start(context=131072)
        history = []
        for i in range(100):
            history.append({"role": "user", "content": f"USER_{i:03d}: " + "x" * 120})
            history.append({"role": "assistant", "content": f"ANSWER_{i:03d}"})
        self.create(input=history, max_output_tokens=32)
        prompt = self.svc.tok.decode(self.engine.last_prompt)
        for i in range(100):
            self.assertIn(f"USER_{i:03d}", prompt)
            self.assertIn(f"ANSWER_{i:03d}", prompt)

    def test_reasoning_encrypted_replay_and_tampering(self):
        self.start(["thinking</think>\n\nREADY", "other</think>\n\nDONE"])
        result = self.create(reasoning={"effort": "low"}, include=["reasoning.encrypted_content"], store=False)
        r = result.output[0].model_dump(exclude_none=True)
        self.assertEqual(r["type"], "reasoning")
        self.assertNotIn("thinking", r["encrypted_content"])
        r.pop("content")
        second = self.create(input=[r, result.output[1].model_dump(exclude_none=True), {"role": "user", "content": "again"}])
        self.assertIn("thinking", self.svc.tok.decode(self.engine.last_prompt))
        r["encrypted_content"] += "x"
        with self.assertRaises(BadRequestError):
            self.create(input=[r])

    def test_reasoning_stream_official_events(self):
        self.start("work</think>\n\nDONE")
        events = self.events(reasoning={"effort": "medium"})
        self.assertTrue(any(e["type"] == "response.reasoning_text.done" for e in events))
        self.assertEqual(events[-1]["response"]["output"][0]["content"][0]["text"], "work")

    def test_reasoning_summary_is_generated_and_accounted(self):
        self.start(["detailed thoughts</think>\n\nDONE", "A short summary."])
        events = self.events(reasoning={"effort": "low", "summary": "concise"})
        result = events[-1]["response"]
        self.assertEqual(result["output"][0]["summary"][0]["text"], "A short summary.")
        self.assertTrue(any(e["type"] == "response.reasoning_summary_text.delta" for e in events))
        self.assertGreater(result["usage"]["input_tokens"], len(self.engine.last_prompt))

    def test_sdk_stream_helper_accumulates_the_same_output(self):
        self.start("hello")
        with self.client.responses.stream(model="test-model", input="hi", reasoning={"effort": "none"}) as stream:
            list(stream)
            result = stream.get_final_response()
        self.assertEqual(result.output_text, "hello")
        Response.model_validate(result.model_dump())

    def test_compaction_sdk_encrypted_state_replay(self):
        from openai.types.responses import CompactedResponse
        self.start(["Task: remember Berlin. Next: report its weather.", "DONE"])
        result = self.client.responses.compact(model="test-model", input=[{"role": "user", "content": "Remember Berlin"},
                         {"role": "assistant", "content": "Remembered."}])
        CompactedResponse.model_validate(result.model_dump())
        self.assertNotIn("Berlin", result.output[0].encrypted_content)
        self.create(input=[{"role": "user", "content": "STALE_PREFIX"}, result.output[0].model_dump(),
                           {"role": "user", "content": "Continue"}])
        prompt = self.svc.tok.decode(self.engine.last_prompt)
        self.assertIn("remember Berlin", prompt)
        self.assertNotIn("STALE_PREFIX", prompt)
        self.assertEqual(self.svc.responses.store.active, 0)

    def test_client_tool_search_loads_deferred_tools_on_replay(self):
        self.start([call("__strata_tool_search", "query", "weather"), call()])
        fn = deepcopy(FUNCTION)
        fn["defer_loading"] = True
        search = {"type": "tool_search", "execution": "client"}
        result = self.create(tools=[search, fn])
        self.assertEqual(result.output[0].type, "tool_search_call")
        first_prompt = self.svc.tok.decode(self.engine.last_prompt)
        self.assertNotIn("Look up a city", first_prompt)
        ts = result.output[0].model_dump(exclude_none=True)
        next_result = self.create(tools=[search, fn], input=[ts, {"type": "tool_search_output",
                       "execution": "client", "call_id": ts["call_id"], "tools": [FUNCTION]}])
        self.assertEqual(next_result.output[0].name, "lookup")
        self.assertIn("Look up a city", self.svc.tok.decode(self.engine.last_prompt))

    def test_store_quota_fails_with_a_terminal_event(self):
        self.start("x" * 4000, store=Store(max_record_bytes=6000))
        result = self.create(max_output_tokens=6000)
        self.assertEqual(result.status, "failed")
        self.assertIn("storage_limit_exceeded", result.error.message)
        self.assertEqual(self.svc.responses.store.active, 0)

    def test_active_admission_limit_is_explicit(self):
        self.start("hello" * 300, delay=0.01, store=Store(max_active=1))
        first = self.create(background=True)
        status, raw = self.wire("/v1/responses", {"model": "test-model", "input": "other"})
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(raw)["error"]["code"], "rate_limit_exceeded")
        self.client.responses.cancel(first.id)

    def test_cancelled_replay_does_not_emit_nonstandard_event_types(self):
        self.start("x" * 2000, delay=0.01)
        result = self.create(background=True)
        self.client.responses.cancel(result.id)
        record = self.svc.responses.store.get(result.id)
        for event in self.svc.responses.replay(record):
            if event:
                EVENT.validate_python(event)

    def test_abandoned_generator_releases_admission_slot(self):
        self.start()
        record, generator = self.svc.responses.create({"model": "test-model", "input": "hi"})
        generator.close()
        self.svc.responses.abandon(record)
        self.assertEqual(self.svc.responses.store.active, 0)
        self.assertTrue(record.finished)

    def test_request_size_and_duplicate_json_keys(self):
        self.start()
        req = urllib.request.Request(self.base + "/v1/responses", data=b'{}', headers={"Content-Length": str(9 * 1024 * 1024)})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(e.exception.code, 413)
        req = urllib.request.Request(self.base + "/v1/responses", data=b'{"model":"a","model":"b"}')
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(e.exception.code, 400)

    def test_function_identity_and_previous_response_tool_result(self):
        self.start([call(), "20°C"])
        first = self.create(tools=[FUNCTION], tool_choice="required")
        fn = first.output[0]
        self.assertEqual(fn.name, "lookup")
        self.assertEqual(json.loads(fn.arguments), {"city": "Berlin"})
        second = self.create(previous_response_id=first.id, input=[{"type": "function_call_output",
                             "call_id": fn.call_id, "output": "20°C"}], tools=[FUNCTION])
        self.assertEqual(second.output_text, "20°C")
        prompt = self.svc.tok.decode(self.engine.last_prompt)
        self.assertIn("Berlin", prompt)
        self.assertIn("20°C", prompt)

    def test_tool_stream_sdk_contract(self):
        self.start(call())
        events = self.events(tools=[FUNCTION])
        fn = events[-1]["response"]["output"][0]
        deltas = [e for e in events if e["type"] == "response.function_call_arguments.delta"]
        self.assertEqual("".join(e["delta"] for e in deltas), fn["arguments"])
        self.assertEqual(deltas[0]["item_id"], fn["id"])

    def test_parallel_replay_outputs_by_call_id(self):
        self.start([call(value="Berlin") + call(value="Paris"), "DONE"])
        first = self.create(tools=[FUNCTION])
        history = [x.model_dump(exclude_none=True) for x in first.output]
        history += [{"type": "function_call_output", "call_id": x.call_id, "output": x.arguments}
                    for x in reversed(first.output)]
        self.assertEqual(self.create(input=history, tools=[FUNCTION]).output_text, "DONE")

    def test_namespace_description_occurs_once(self):
        self.start(call("weather.lookup"))
        other = deepcopy(FUNCTION)
        other["name"] = "other"
        definitions = [{"type": "namespace", "name": "weather", "description": "UNIQUE_SCOPE_DESCRIPTION",
                        "tools": [FUNCTION, other]}]
        result = self.create(tools=definitions)
        self.assertEqual(result.output[0].namespace, "weather")
        self.assertEqual(result.output[0].name, "lookup")
        self.assertEqual(self.svc.tok.decode(self.engine.last_prompt).count("UNIQUE_SCOPE_DESCRIPTION"), 1)

    def test_general_custom_tool_grammar_and_replay(self):
        self.start([call("exec", "input", "print(1)"), "DONE"])
        definition = {"type": "custom", "name": "exec", "format": {"type": "grammar", "syntax": "lark",
                      "definition": 'start: "print(" INT ")"\n%import common.INT'}}
        events = self.events(tools=[definition])
        item = events[-1]["response"]["output"][0]
        self.assertEqual(item["type"], "custom_tool_call")
        self.assertEqual(item["input"], "print(1)")
        self.assertEqual(self.create(tools=[definition], input=[item, {"type": "custom_tool_call_output",
                         "call_id": item["call_id"], "output": "1"}]).output_text, "DONE")

    def test_assistant_tool_only_replay_supplies_an_empty_model_user_boundary(self):
        self.start(call())
        first = self.create(tools=[FUNCTION])
        self.create(input=[first.output[0].model_dump(exclude_none=True), {"type": "function_call_output",
                          "call_id": first.output[0].call_id, "output": "done"}], tools=[FUNCTION])
        prompt = self.svc.tok.decode(self.engine.last_prompt)
        self.assertIn("<|im_start|>user\n", prompt)

    def test_instruction_only_request(self):
        self.start()
        self.assertEqual(self.create(input=[], instructions="Reply READY.").output_text, "READY")

    def test_codex_additional_tools(self):
        self.start(call("functions.exec", "input", "text(1)"))
        definition = {"type": "namespace", "name": "functions", "description": "Execute code",
                      "tools": [{"type": "custom", "name": "exec", "format": {"type": "grammar", "syntax": "lark",
                      "definition": r'start: SOURCE' + '\nSOURCE: /[\\s\\S]+/\n'}}]}
        result = self.create(input=[{"type": "additional_tools", "role": "developer", "tools": [definition]},
                                   {"role": "user", "content": "run code"}],
                             extra_body={"reasoning": {"effort": "none", "context": "all_turns"}})
        self.assertEqual(result.output[0].namespace, "functions")
        self.assertEqual(result.output[0].input, "text(1)")

    def test_invalid_tool_schema_is_not_emitted(self):
        self.start(call(parameter="wrong"))
        events = self.events(tools=[FUNCTION])
        self.assertEqual(events[-1]["type"], "response.failed")
        self.assertEqual(events[-1]["response"]["output"], [])
        Response.model_validate(events[-1]["response"])

    def test_tool_choice_and_parallel_constraints(self):
        for script, options in [("plain", {"tool_choice": "required"}), (call(), {"tool_choice": "none"}),
                                (call() + call(), {"parallel_tool_calls": False})]:
            with self.subTest(options=options):
                self.start(script)
                result = self.create(tools=[FUNCTION], **options)
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.output, [])

    def test_custom_grammar_failure(self):
        self.start(call("custom", "input", "no"))
        result = self.create(tools=[{"type": "custom", "name": "custom",
                                    "format": {"type": "grammar", "syntax": "regex", "definition": "yes"}}])
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output, [])

    def test_json_mode_schema_and_no_invalid_success(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"], "additionalProperties": False}
        fmt = {"type": "json_schema", "name": "counter", "schema": schema, "strict": True}
        for script, valid in [('{"n":1}', True), ('{"n":"x"}', False), ('{"n":NaN}', False),
                              ('{"n":1,"n":2}', False), ("hello", False)]:
            with self.subTest(script=script):
                self.start(script)
                result = self.create(text={"format": fmt})
                self.assertEqual(result.status, "completed" if valid else "failed")
                if not valid:
                    self.assertEqual(result.output, [])
        self.start('{"ok":true}')
        self.assertEqual(self.create(text={"format": {"type": "json_object"}}).status, "completed")

    def test_incomplete_budget_does_not_execute_repaired_call(self):
        self.start(call())
        result = self.create(tools=[FUNCTION], max_output_tokens=35)
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.incomplete_details.reason, "max_output_tokens")
        self.assertFalse(any(x.type == "function_call" for x in result.output))

    def test_eos_inside_xml_call_is_not_repaired_into_success(self):
        self.start('<tool_call><function=lookup><parameter=city>Berlin</parameter>')
        result = self.create(tools=[FUNCTION])
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output, [])

    def test_disconnect_after_terminal_event_preserves_stored_completion(self):
        self.start()
        record, generator = self.svc.responses.create({"model": "test-model", "input": "hi"})
        for event in generator:
            if event and event["type"] == "response.completed":
                break
        generator.close()
        self.assertEqual(self.svc.responses.store.get(record.response["id"]).snapshot()["status"], "completed")
        self.assertEqual(self.svc.responses.store.active, 0)

    def test_context_error_before_headers(self):
        self.start(context=600)
        with self.assertRaises(BadRequestError) as e:
            self.create(input="x" * 1000)
        self.assertEqual(e.exception.code, "context_length_exceeded")
        self.assertEqual(self.svc.responses.store.active, 0)

    def test_invalid_requests_are_400_without_inference(self):
        self.start()
        cases = [{"max_output_tokens": -1}, {"max_output_tokens": True}, {"temperature": "bad"},
                 {"background": True, "store": False}, {"extra_body": {"conversation": "conv_x"}},
                 {"tools": [{"type": "web_search"}]}, {"tools": [{"type": "function", "name": "bad.name"}]},
                 {"input": [{"type": "function_call_output", "call_id": "unknown", "output": "bad"}]},
                 {"text": {"format": {"type": "json_schema", "name": "bad", "schema": {"$ref": "https://x"}}}}]
        for options in cases:
            with self.subTest(options=options), self.assertRaises(BadRequestError):
                self.create(**options)
        self.assertEqual(self.engine.last_prompt, [])

    def test_unknown_model_rejected(self):
        self.start()
        with self.assertRaises(NotFoundError):
            self.create(model="wrong")

    def test_instructions_not_inherited_by_previous_id(self):
        self.start()
        first = self.create(instructions="FIRST_INSTRUCTION", input="FIRST_USER")
        self.create(previous_response_id=first.id, instructions="SECOND_INSTRUCTION", input="SECOND_USER")
        prompt = self.svc.tok.decode(self.engine.last_prompt)
        self.assertNotIn("FIRST_INSTRUCTION", prompt)
        self.assertIn("FIRST_USER", prompt)
        self.assertIn("SECOND_INSTRUCTION", prompt)

    def test_input_items_pagination_and_token_count(self):
        self.start()
        result = self.create(input=[{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"},
                                    {"role": "user", "content": "three"}])
        first = self.client.responses.input_items.list(result.id, limit=1, order="asc")
        self.assertEqual(first.data[0].content[0].text, "one")
        self.assertTrue(first.has_more)
        second = self.client.responses.input_items.list(result.id, limit=1, order="asc", after=first.last_id)
        self.assertEqual(second.data[0].content[0].text, "two")
        status, body = self.wire("/v1/responses/input_tokens", {"model": "test-model", "input": "hi"})
        self.assertEqual(status, 200)
        self.assertGreater(json.loads(body)["input_tokens"], 2)

    def test_background_retrieval_and_event_replay(self):
        self.start("hello" * 20, delay=0.002)
        result = self.create(background=True)
        self.assertIn(result.status, ("queued", "in_progress"))
        record = self.svc.responses.store.get(result.id)
        events = list(self.svc.responses.replay(record))
        self.assertEqual(events[-1]["type"], "response.completed")
        self.assertEqual(self.client.responses.retrieve(result.id).output_text, "hello" * 20)
        status, body = self.wire(f"/v1/responses/{result.id}?stream=true&starting_after=1")
        self.assertEqual(status, 200)
        replay = [json.loads(line[6:]) for line in body.decode().splitlines() if line.startswith("data: ")]
        self.assertTrue(all(e["sequence_number"] > 1 for e in replay))

    def test_background_cancel_and_queue(self):
        self.start("hello" * 200, delay=0.005)
        first = self.create(background=True)
        second = self.create(background=True)
        self.client.responses.cancel(second.id)
        self.client.responses.cancel(first.id)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            a, b = self.client.responses.retrieve(first.id), self.client.responses.retrieve(second.id)
            if a.status == b.status == "cancelled":
                break
            time.sleep(0.01)
        self.assertEqual((a.status, b.status), ("cancelled", "cancelled"))

    def test_store_eviction_and_expiry(self):
        self.start(store=Store(max_records=1, ttl=0.05))
        first = self.create()
        second = self.create()
        with self.assertRaises(NotFoundError):
            self.client.responses.retrieve(first.id)
        self.assertEqual(self.client.responses.retrieve(second.id).status, "completed")
        time.sleep(0.06)
        with self.assertRaises(NotFoundError):
            self.client.responses.retrieve(second.id)

    def test_images_fail_clearly_without_vision(self):
        self.start()
        with self.assertRaises(BadRequestError):
            self.create(input=[{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,YQ=="}]}])

    def test_auth_applies_to_all_response_routes(self):
        self.start()
        self.svc.api_key = "secret"
        for path, method in [("/v1/responses", "POST"), ("/v1/responses/resp_x", "GET"), ("/responses/resp_x", "DELETE")]:
            self.assertEqual(self.wire(path, {} if method == "POST" else None, method)[0], 401)


if __name__ == "__main__":
    unittest.main()
