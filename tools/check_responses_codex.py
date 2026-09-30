"""Codex CLI integration against native Strata. Provider overrides are process-only.

Uses a fresh temporary Codex home and a read-only image-tool round trip.
Pass --catalog for the local model's existing Codex model metadata.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request

from PIL import Image


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cli", default="codex")
    p.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    p.add_argument("--model")
    p.add_argument("--catalog")
    p.add_argument("--code-mode", action="store_true", help="Use code_mode_only in an ephemeral model catalog")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--work-dir", default="work")
    args = p.parse_args()
    request = urllib.request.Request(args.base_url.rstrip("/") + "/models")
    model = args.model or json.loads(urllib.request.urlopen(request, timeout=10).read())["data"][0]["id"]
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    def request_count():
        status = json.loads(urllib.request.urlopen(args.base_url.rstrip("/") + "/status", timeout=10).read())
        return status["activity"]["requests"]
    before = request_count()
    with tempfile.TemporaryDirectory(prefix="responses-codex-", dir=work) as temp:
        home = Path(temp) / "home"
        home.mkdir()
        fixture = Path(temp) / "responses-image.png"
        Image.new("RGB", (64, 64), (255, 0, 0)).save(fixture)
        config = {"model_provider": "strata_test", "model_reasoning_effort": "none", "notify": [],
                  "web_search": "disabled", "model_providers.strata_test.name": "Strata native Responses test",
                  "model_providers.strata_test.base_url": args.base_url.rstrip("/"),
                  "model_providers.strata_test.wire_api": "responses",
                  "model_providers.strata_test.requires_openai_auth": False,
                  "model_providers.strata_test.supports_websockets": False,
                  "model_providers.strata_test.request_max_retries": 0}
        if args.catalog:
            catalog_path = Path(args.catalog).resolve()
            if args.code_mode:
                catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                entry = next(m for m in catalog["models"] if m.get("slug") == model)
                entry["tool_mode"] = "code_mode_only"
                catalog_path = home / "models-test.json"
                catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            config["model_catalog_json"] = str(catalog_path)
        elif args.code_mode:
            raise SystemExit("--code-mode requires --catalog")
        cmd = [args.cli, "exec", "--ephemeral", "--skip-git-repo-check", "--color", "never",
               "--sandbox", "read-only", "-C", temp, "-m", model, "--json"]
        for k, v in config.items():
            cmd += ["-c", k + "=" + json.dumps(v)]
        image_args = json.dumps({"path": str(fixture)})
        prompt = ("Use functions.exec once to run this JavaScript: const result = await tools.view_image(" + image_args +
                  '); image(result.image_url); text("IMAGE_READ"); ' if args.code_mode else
                  "Call view_image once with these arguments: " + image_args + ". ")
        cmd.append(prompt + "Then reply exactly READY. "
                   "Do not call terminal tools or change any files.")
        result = subprocess.run(cmd, env={**os.environ, "CODEX_HOME": str(home)}, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=args.timeout)
        events = []
        for line in result.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        items = [e.get("item", {}) for e in events if e.get("type") == "item.completed"]
        messages = [item.get("text", "") for item in items if item.get("type") == "agent_message"]
        requests = request_count() - before
        # Some internal tools do not produce JSON item events. A tool call and its
        # result still require two real model requests. Reject CLI execution errors.
        tool_error = "codex_core::tools::router: error=" in result.stderr
        ok = result.returncode == 0 and requests == 2 and not tool_error and bool(messages) and \
             messages[-1].strip().rstrip(".") == "READY"
        print(json.dumps({"ok": ok, "model": model, "exit_code": result.returncode,
                          "code_mode": args.code_mode,
                          "engine_requests": requests, "last_message": messages[-1] if messages else None,
                          "events": len(events)}, ensure_ascii=False), flush=True)
        if not ok:
            print(result.stdout[-6000:])
            print(result.stderr[-2000:])
            raise SystemExit(1)


if __name__ == "__main__":
    main()
