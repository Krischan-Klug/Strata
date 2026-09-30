# Native Responses API

Strata serves `/v1/responses` directly through its existing tokenizer, model chat
template, vision encoder and resident engine. There is no HTTP call to Chat
Completions and no LiteLLM dependency. Chat Completions and Anthropic remain
available on their existing routes.

This implementation targets the OpenAI Responses wire contract as inspected on
2026-09-30 and tested with **openai-python 2.54.0**. The explicit capability table
below defines the supported subset; this is not a claim to implement OpenAI's
hosted services. Unsupported operations return a parameter-specific error.

## Run and test

An existing installation needs these additional frontend packages once:

```powershell
.venv\Scripts\python.exe -m pip install -r serve/responses/requirements.txt
.venv\Scripts\python.exe -m serve.server --engine strata --config strata-swift-iq3_xxs.json --port 8090
```

New installations get these packages through `setup.py`. No engine rebuild is
needed. Use the config and port of your own installation.

For GPU-free HTTP, lifecycle and SDK contract tests:

```powershell
.venv\Scripts\python.exe -m pip install -r serve/responses/requirements-test.txt
.venv\Scripts\python.exe -m unittest discover -s serve -p "test_*.py"
```

For a running real engine:

```powershell
.venv\Scripts\python.exe tools/check_responses.py --base-url http://127.0.0.1:8090/v1
```

The smoke test runs text, SSE, schema output, function and custom tool round trips,
state retrieval/deletion, encrypted compaction replay and an image request when
vision is loaded. It executes no returned tool code: results are supplied by the
test client. It fails if a required check fails.

For a separately installed Codex CLI, `tools/check_responses_codex.py` tests a
read-only internal tool round trip against the running engine. Pass `--cli`,
`--base-url` and `--catalog` for its executable, provider URL and existing local
model catalog. `--code-mode` changes tool_mode only in a temporary test catalog.
The test does not edit the real Codex configuration. Run it with no other active
model requests: it uses the engine request counter to verify the two-turn cycle.

## Capability and test matrix

| Feature | Behavior | Contract coverage in `serve/test_responses.py` |
| --- | --- | --- |
| `POST /v1/responses` | Text, instructions, system/developer/user/assistant messages; exact model validation | SDK create, unknown model, instruction inheritance |
| SSE | Named events, increasing sequence numbers, stable item IDs, added/delta/done/terminal lifecycle | Text/reasoning/tool event validation against official SDK types, SDK stream accumulator |
| Images | `input_image` HTTP(S)/data URLs in messages and tool results, through existing vision preparation | No-vision error, multimodal round trip and cleanup; real smoke image |
| Sampling and limits | Temperature, top-p, output budget (including reasoning), explicit context overflow error; no prompt truncation | Context, budgets, invalid requests |
| Reasoning | Effort, raw `reasoning_text`, authenticated encrypted replay, optional model-generated summaries | Reasoning stream, encrypted replay/tampering, summary events and usage |
| Function tools | Schema parameters, stable call IDs, multiple calls and corresponding output replay | Function/SSE/parallel replay |
| `tool_choice` | auto, none, required, function/custom selection and allowed function/custom tool list | Required/none/specific/parallel validation |
| Namespaces | Shared guidance once, qualified model name, original name + namespace on wire | Namespace count/identity |
| Custom tools | General raw-string `input`, text or regex/Lark grammar; not restricted to apply_patch | General grammar/replay/failure, Codex grammar fixtures |
| JSON output | `text.format`: text, json_object, json_schema; successful constrained output is validated | Valid JSON/schema, invalid JSON/types, duplicate keys and NaN |
| Store | default true; false retains no response/history/events; retrieve/delete/list input items | SDK retrieve/delete/unstored, pagination, eviction/expiry |
| `previous_response_id` | Replays stored input/output; prior `instructions` are not inherited | Tool round trip, instruction replacement |
| Background | Queued jobs, retrieval, cancellation, resumable event replay | Background queue/cancel/replay; admission and quota tests |
| Input token count | `POST /v1/responses/input_tokens` uses the actual template/tokenizer/vision path | Count and image cleanup |
| Compaction | `POST /v1/responses/compact`: local model summary in authenticated encrypted item, replayable without storage | SDK compact, encryption, replay and prefix replacement |
| Tool search | `execution: client`, deferred definitions, tool_search_call/output replay | Client search and deferred loading |
| Codex transport | additional_tools developer items, namespaced exec, custom apply_patch, reasoning.context, advisory client_metadata | Code-mode input, actual Codex request fixtures and CLI integration |

The `/responses` aliases work too. All these routes use the existing server API
key authorization. Foreground disconnect cancels generation; disconnecting from
background retrieval leaves the job running. A cancelled background stream closes;
retrieve its final `status: cancelled` via GET (there is no invented cancellation
SSE event).

## Validation and generation guarantees

Strata's released decoder has no grammar/logit masking interface. JSON/schema
outputs and tool-bearing responses are therefore buffered and validated before
output is published. They still use the same valid SSE event lifecycle. Heartbeat
comments keep connections alive during buffered generation.

Invalid complete JSON, strict function parameters, custom grammar output, forbidden
tool choices or excess calls with `parallel_tool_calls=false` produce
`response.failed` with `error.code: server_error` and a diagnostic in the message.
They cannot be returned as successful tool calls or successful structured output.
There is no hidden retry and no assertion that the decoder itself was constrained.
For `strict: false` function declarations only structural JSON is required. JSON
Schema output is validated even if strict is omitted.

An output budget exhaustion produces `response.incomplete`. The parser's repaired
partial XML call is never emitted as an executable tool call. Partial plain/JSON
text may be returned with incomplete status. Clients must check response status.

JSON schemas use Draft 2020-12 and document-local references. Remote references and
resource identifiers are rejected. Lark uses its LALR parser; only bundled
`common` terminal imports are allowed. Codex's optional `/(.*)/` line terminal is
expressed as an equivalent optional nonempty terminal for Lark's runtime lexer.
Regex custom grammars use the existing `regex` package with a validation timeout.
Unsupported/invalid grammars fail before inference.

Reasoning summaries are a separate local generation pass, not raw reasoning
relabeled as a summary. Its tokens are included in Usage. Reasoning token usage
counts the generation tokens while the model parser is in the reasoning phase,
including phase delimiters; a token spanning a boundary belongs to its starting
phase. Cached usage comes from the engine's actual reused prefix count.

Compaction is a model-generated summary, not a lossless encoding of the transcript.
The returned window is canonical: pass its output to the next request. The latest
authenticated compaction item replaces preceding transcript items while retaining
additional tool declarations. Images can inform a compaction pass through the
existing encoder. The summary's quality depends on the loaded model. Automatic
`context_management` threshold compaction is not implemented.

## State and resource bounds

State is process-local and cleared on restart. Defaults: 128 stored records,
64 MiB retention accounting, 16 MiB per record, one-hour idle expiry, four active
Responses requests. Completed records are evicted oldest first under pressure;
active jobs are not evicted. A request above the 8 MiB HTTP body limit gets 413;
full admission/storage capacity gets 429. Exceeding event retention during a job
fails that job explicitly. Storage-disabled requests retain no replay history.
The existing engine FIFO serializes all API routes.

Reasoning and compaction envelopes use Fernet authenticated encryption and are
bound to the model. A random process key is used by default. To replay encrypted
state across restarts, provide a stable Fernet key via `STRATA_RESPONSES_KEY`.
Generate one with `cryptography.fernet.Fernet.generate_key()` and keep it private.
This does not make stored response IDs persistent. A server API key is a single
access boundary, not an OpenAI multi-tenant account system.

`prompt_cache_key`, `safety_identifier`, `user` and Codex `client_metadata` are
accepted advisory client fields. Prefix reuse remains the resident engine's own
cache; no OpenAI cache-retention or moderation behavior is promised. `service_tier`
auto/default maps to the local default service. Metadata is retained on responses.

## Explicitly unsupported

- OpenAI-hosted web/file search, code interpreter, shell/computer/image-generation
  services, reusable cloud prompts, moderation and conversation resources.
- Server-executed tool search and provider-executed remote MCP approval/call flows.
  Codex-owned MCP tools work as client function/custom tools, including namespaces;
  Strata's separate existing MCP integration remains available through Chat.
- Audio/video/file-ID inputs, logprobs, automatic truncation, cache-retention tiers,
  automatic compaction, WebSocket transport and event obfuscation.

These need actual implementations/backends rather than accepted-but-ignored flags.
Requests containing them are rejected. Native Responses does not by itself improve
Qwen's reasoning, grammar reliability or code-mode tool selection.

## Codex provider

The test provider can point directly at Strata:

```toml
[model_providers.strata]
name = "Strata native Responses"
base_url = "http://127.0.0.1:8090/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
```

Select the exact model ID returned by `/v1/models`. If server authentication is
enabled, use the provider's `env_key` for a Bearer key. Keep WebSockets disabled for
this implementation. Model catalog choices for code mode/tool search are separate
from the provider transport.

## Implementation and references

`serve/responses/input.py` handles contracts and template normalization;
`constraints.py` validates schemas/grammars; `events.py` owns the output lifecycle;
`store.py` owns bounded state; `service.py` orchestrates engine work; `http.py` is
the HTTP adapter. `server.py` only delegates the new routes and reports reasoning
token counts from the shared engine path.

Primary references:

- [Responses create](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [Streaming Responses](https://developers.openai.com/api/docs/guides/streaming-responses)
- [Function and custom tools](https://developers.openai.com/api/docs/guides/function-calling)
- [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
- [Compaction](https://developers.openai.com/api/docs/guides/compaction)
- [Tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)

vLLM (Apache-2.0) and Unsloth Studio were inspected as behavioral references.
No implementation code was copied from either project; in particular no Studio
AGPL source was incorporated into Strata's MIT code.
