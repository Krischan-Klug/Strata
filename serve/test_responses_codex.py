"""Opt-in actual Codex client tests with scripted inference (no model/GPU).

Set STRATA_CODEX_CLI and STRATA_CODEX_CATALOG to a Codex executable and its
model catalog, then run python -m unittest serve.test_responses_codex.
All client config, MCP processes and file changes are confined to a temp directory.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, MockEngine, Service, Vision, serve
from serve.test_responses import call
from serve import test_server

ROOT = Path(__file__).resolve().parents[1]
CLI, CATALOG = os.environ.get("STRATA_CODEX_CLI"), os.environ.get("STRATA_CODEX_CATALOG")


@unittest.skipUnless(CLI and CATALOG, "set STRATA_CODEX_CLI and STRATA_CODEX_CATALOG for actual client tests")
class CodexResponses(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="strata-codex-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.home = self.work / "home"
        self.home.mkdir()
        self.image = self.work / "screenshot.png"
        Image.new("RGB", (64, 64), (255, 0, 0)).save(self.image)
        self.catalog = json.loads(Path(CATALOG).read_text(encoding="utf-8"))
        model = self.catalog["models"][0]
        model.update(slug="test-model", base_instructions="Follow the user's instructions.", model_messages=None,
                     context_window=262144, max_context_window=262144, default_reasoning_level="none",
                     include_skills_usage_instructions=False, include_plugin_usage_instructions=False,
                     include_apps_usage_instructions=False, tool_mode=None, supports_search_tool=False)
        self.catalog["models"] = [model]
        tok = ByteTokenizer()
        self.engine = MockEngine(tok, ["READY", "READY"], max_context=262144)
        vision_dir = self.work / "vision"
        vision_dir.mkdir()
        self.vision = test_server.ImageMarkers.FakeVision(vision_dir)
        self.sources = []
        encode = self.vision.encode
        def checked_image(source):
            self.sources.append(source)
            self.assertTrue(Vision.load(source).startswith(b"\x89PNG"))
            return encode(source)
        self.vision.encode = checked_image
        self.svc = Service(self.engine, tok, ChatTemplate(ROOT / "serve/chat_template.jinja"),
                           model_name="test-model", vision=self.vision)
        self.http = serve(self.svc, port=0)
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.addCleanup(self.svc.responses.store.close)

    def run_client(self, script, prompt, *, code_mode=False, extra=None, expected_rejection=None):
        requests = []
        original = self.svc.responses._spec
        def capture(req):
            spec = original(req)
            requests.append(req)
            if len(requests) == 1:
                rendered = script(spec["tools"].by_name)
                self.engine.scripts[0] = self.svc.tok.encode(rendered, parse_special=True) + self.svc.tok.encode("<|im_end|>", parse_special=True)
            return spec
        self.svc.responses._spec = capture
        self.catalog["models"][0]["tool_mode"] = "code_mode_only" if code_mode else None
        catalog = self.home / "models.json"
        catalog.write_text(json.dumps(self.catalog), encoding="utf-8")
        config = {"model_provider": "test", "model_reasoning_effort": "none", "model_catalog_json": str(catalog),
                  "web_search": "disabled", "notify": [], "model_providers.test.name": "Strata test",
                  "model_providers.test.base_url": f"http://127.0.0.1:{self.http.server_address[1]}/v1",
                  "model_providers.test.wire_api": "responses", "model_providers.test.requires_openai_auth": False,
                  "model_providers.test.supports_websockets": False, "model_providers.test.request_max_retries": 0}
        config.update(extra or {})
        cmd = [CLI, "exec", "--ephemeral", "--skip-git-repo-check", "--color", "never", "--json",
               "--sandbox", "read-only", "-C", str(self.work), "-m", "test-model"]
        for key, value in config.items():
            cmd += ["-c", key + "=" + json.dumps(value)]
        cmd.append(prompt + " Then reply exactly READY.")
        result = subprocess.run(cmd, env={**os.environ, "CODEX_HOME": str(self.home)}, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        if expected_rejection:
            self.assertIn(expected_rejection, result.stderr)
        else:
            self.assertNotIn("codex_core::tools::router: error=", result.stderr)
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        messages = [ev["item"].get("text", "") for ev in events if ev.get("type") == "item.completed"
                    and ev["item"].get("type") == "agent_message"]
        self.assertEqual(messages[-1].strip(), "READY")
        self.assertEqual(self.engine.turns, 2, result.stdout[-3000:])
        self.assertEqual(len(requests), 2)
        self.assertTrue(any(item.get("type") in ("function_call_output", "custom_tool_call_output")
                            for item in requests[1]["input"]))
        return requests

    @staticmethod
    def tool_name(tools, leaf):
        return next(key for key, tool in tools.items() if tool["name"] == leaf)

    def test_view_image_result_reaches_vision(self):
        self.run_client(lambda tools: call(self.tool_name(tools, "view_image"), "path", str(self.image)),
                        f"Read the image at {self.image} with view_image.")
        self.assertEqual(len(self.sources), 1)
        self.assertFalse(Path(self.engine.last_embeddings).exists())

    def test_code_mode_image_result_reaches_vision(self):
        js = 'const result = await tools.view_image(' + json.dumps({"path": str(self.image)}) + '); image(result.image_url);'
        self.run_client(lambda tools: call(self.tool_name(tools, "exec"), "input", js),
                        "Use functions.exec to run: " + js, code_mode=True)
        self.assertEqual(len(self.sources), 1)

    @unittest.skipUnless(os.environ.get("STRATA_CODEX_EXECUTION_TESTS"), "terminal execution requires a client policy that permits it")
    def test_terminal_round_trip(self):
        self.run_client(lambda tools: call(self.tool_name(tools, "exec_command"), "cmd", "echo STRATA_TERMINAL_OK"),
                        "Run echo STRATA_TERMINAL_OK in the terminal.")
        self.assertIn("STRATA_TERMINAL_OK", self.svc.tok.decode(self.engine.last_prompt))

    def test_custom_apply_patch_denial_round_trip(self):
        patch = "*** Begin Patch\n*** Add File: ready.txt\n+STRATA_PATCH_OK\n*** End Patch\n"
        self.run_client(lambda tools: call(self.tool_name(tools, "apply_patch"), "input", patch),
                        "Use apply_patch to create ready.txt containing STRATA_PATCH_OK.",
                        expected_rejection="patch rejected:")
        self.assertFalse((self.work / "ready.txt").exists())
        self.assertIn("patch rejected:", self.svc.tok.decode(self.engine.last_prompt))

    def test_client_mcp_screenshot_round_trip(self):
        # Only the client owns this MCP process; the inference server receives ordinary function calls.
        fixture = self.work / "mcp_fixture.py"
        png = base64.b64encode(self.image.read_bytes()).decode()
        fixture.write_text('''import json, sys
for line in sys.stdin:
    req = json.loads(line)
    if "id" not in req: continue
    method = req["method"]
    if method == "initialize":
        result = {"protocolVersion": req["params"]["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "strata-test", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "screenshot", "description": "Return a test screenshot",
                            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                            "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "STRATA_MCP_OK"},
                              {"type": "image", "mimeType": "image/png", "data": ''' + repr(png) + '''}]}
    else: result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
''', encoding="utf-8")
        def script(tools):
            key = next(key for key, tool in tools.items() if "screenshot" in tool["name"])
            return f"<tool_call><function={key}></function></tool_call>"
        self.run_client(script, "Call the MCP screenshot tool once.",
                        extra={"mcp_servers.fixture.command": sys.executable, "mcp_servers.fixture.args": [str(fixture)]})
        self.assertEqual(len(self.sources), 1, self.svc.tok.decode(self.engine.last_prompt)[-2000:])
        self.assertIn("STRATA_MCP_OK", self.svc.tok.decode(self.engine.last_prompt))


if __name__ == "__main__":
    unittest.main()
