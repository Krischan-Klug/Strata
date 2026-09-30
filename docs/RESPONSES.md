# Responses API

- Base URL: `http://127.0.0.1:8080/v1` (adjust to your server).
- Model: use the exact ID returned by `GET /v1/models`.
- Authentication: use the server API key when enabled.
- API reference: [OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create).

## Setup

- New installations include the Responses dependencies through `setup.py`.
- Existing installations: install the dependencies, then start Strata (replace `model-config.json` with your configuration).

```powershell
.venv\Scripts\python.exe -m pip install -r serve/responses/requirements.txt
.venv\Scripts\python.exe -m serve.server --engine strata --config model-config.json --port 8080
```

## Features

- Text, instructions, message history and image input (HTTP(S) or data URLs; requires vision).
- Streaming through SSE with item IDs and numbered events.
- Sampling settings, output token limits and token usage, including reasoning.
- Reasoning effort, reasoning text, encrypted replay and model-generated summaries.
- Reasoning context: `current_turn` preserves the active tool round; `all_turns` preserves earlier reasoning too. `auto` uses the local template default, `all_turns`; responses report the effective setting.
- Function tools, namespaces and custom tools with text, regex or Lark input.
- Client-side shell, patching and computer use through function/custom tools, including screenshot results.
- Tool selection, parallel calls and tool-result replay through `call_id`.
- Structured output through `text.format`: `text`, `json_object` or `json_schema`.
- Stored responses, `previous_response_id`, background generation and cancellation.
- Input token counting and compaction into a replayable summary.
- Client-executed tool search with deferred tools; Codex Code Mode tool declarations.

## Endpoints

- `POST /v1/responses` — create a response; set `stream: true` for SSE.
- `GET /v1/responses/{id}` — retrieve a stored response.
- `DELETE /v1/responses/{id}` — delete a stored response.
- `GET /v1/responses/{id}/input_items` — list input items with pagination.
- `POST /v1/responses/{id}/cancel` — cancel a background response.
- `POST /v1/responses/input_tokens` — count input tokens.
- `POST /v1/responses/compact` — compact conversation history.

## Usage and limits

- Check response status: output budget exhaustion returns `incomplete`; invalid generated structured output or tool calls return `failed`.
- JSON and tool responses are buffered and validated before publication, including when streaming. Decoder-level grammar enforcement is unavailable.
- JSON Schema supports Draft 2020-12 with document-local references. Lark supports LALR and bundled `common` terminal imports.
- `previous_response_id` replays stored history; send `instructions` again on each request.
- `store` defaults to `true`. Background generation requires storage; `store: false` retains no response or replay history.
- Resume background events through `GET /v1/responses/{id}?stream=true&starting_after=<sequence_number>`.
- Use compaction's returned `output` as the next request's history. Compaction summarizes the conversation; automatic compaction and input truncation are unsupported.
- Compaction uses the remaining context, capped at 1,024 output tokens. Context overflow returns `400` with `context_length_exceeded`.
- Assistant `phase` is retained in history; cache/safety identifiers are echoed in responses.
- State is process-local and cleared on restart. Defaults: 128 records, 64 MiB total retention, 16 MiB per record, one-hour idle expiry and four active requests. Completed records are evicted under pressure.
- Request bodies are limited to 8 MiB (`413`); exhausted admission or storage capacity returns `429`.
- Set `STRATA_RESPONSES_KEY` to a stable Fernet key to replay encrypted reasoning and compaction across restarts. Stored response IDs remain process-local.

## Unsupported

- Hosted tools and services: web/file search, code interpreter, image generation, cloud prompts and conversation resources.
- Native `computer`, `computer_use_preview`, `shell`, `local_shell` and `apply_patch` tool types and their call/output items. Client implementations exposed as function/custom tools are supported.
- Provider-executed remote MCP and server-executed tool search. Client-owned tools use function or custom tool calls.
- File/audio/video inputs, logprobs, cache-retention tiers, automatic context management, WebSockets and event obfuscation.
- Pro reasoning mode and mid-conversation configuration updates.
- Unsupported request options return `400` with `unsupported_parameter` and the offending `param`, before SSE starts. Client permission denials return through ordinary tool outputs.

## Codex

```toml
[model_providers.strata]
name = "Strata"
base_url = "http://127.0.0.1:8080/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
```

- Select the model ID from `/v1/models`.
- When server authentication is enabled, set the provider's `env_key` to the environment variable containing the API key.

## Tests

- HTTP, lifecycle and SDK contract tests (tested with `openai-python 2.54.0`):

```powershell
.venv\Scripts\python.exe -m pip install -r serve/responses/requirements-test.txt
.venv\Scripts\python.exe -m unittest discover -s serve -p "test_*.py"
```

- Integration test against a running engine:

```powershell
.venv\Scripts\python.exe tools/check_responses.py --base-url http://127.0.0.1:8080/v1
```

- Codex CLI integration: `tools/check_responses_codex.py --help` lists executable, model catalog and Code Mode options.
- Scripted Codex client tests: set `STRATA_CODEX_CLI` and `STRATA_CODEX_CATALOG`; run `python -m unittest serve.test_responses_codex`. Terminal tests additionally require `STRATA_CODEX_EXECUTION_TESTS=1` and permission in the client.
