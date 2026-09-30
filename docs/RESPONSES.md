# Responses API compatibility

Reference snapshot: **2026-09-30**. Implementation baseline: **`2ee0718`**. Scope: the published stable Responses HTTP, item, tool, SSE and WebSocket contracts, plus separately marked Beta additions and adjacent Conversations resources. SDK aliases with the same JSON shape are listed once; nested paths apply to each array element. Arbitrary JSON Schema, metadata and argument keys are user-defined, not additional API parameters.

Sources: [HTTP and models](https://developers.openai.com/api/reference/python/resources/responses), [SSE](https://developers.openai.com/api/reference/resources/responses/streaming-events), [WebSocket](https://developers.openai.com/api/reference/resources/responses/websocket-events), [Beta](https://developers.openai.com/api/reference/python/resources/beta/subresources/responses/methods/create), [Conversations](https://developers.openai.com/api/reference/python/resources/conversations). The official contract defines compatibility; client-specific behavior does not redefine it.

| Mark | Meaning |
| --- | --- |
| `=` | Direct mapping to an existing engine feature. |
| **Local** | Implemented by the Responses adapter or local service. |
| **Partial** | Accepted with a semantic difference, ignored field or missing variant. |
| **Off** | Not provided; no automatic replacement or redirection. |
| Work: small / adapter / engine / service | Validation or wire change / protocol translation / inference capability / separate execution or storage subsystem. An assessment, not a promise of model support. |

Client-owned function/custom tools can run external programs, MCP tools or computer automation. Strata generates calls and reads their results; the client executes them. These are usable alternatives, **not** automatic translations of native hosted-tool schemas. Unsupported top-level body fields and unsupported tool definitions return HTTP 400 with `error.param` and `code=unsupported_parameter`; the exceptions for ignored nested fields and query options are marked below.

## HTTP operations

| Feature / fields | Official meaning | Strata implementation; remaining work |
| --- | --- | --- |
| `POST /v1/responses` | Generate a response; JSON or SSE. | **Local**; text/vision and client tools. Unversioned `/responses` is an alias. |
| `GET /v1/responses/{response_id}` | Retrieve stored response state. | **Local**; process-local storage; expired, evicted or unknown IDs return 404. |
| Retrieve: `response_id`, `stream`, `starting_after` | Identify a response; stream its events, resuming after a sequence number. | **Local**; stored event replay. `starting_after` is processed only with `stream=true`. |
| Retrieve: `include`, `include_obfuscation` | Select optional data and stream padding. | **Partial**; query options currently ignored. Work: small, validate and apply/reject explicitly. |
| `DELETE /v1/responses/{response_id}` | Delete stored state; reference example returns `id`, `object=response.deleted`, `deleted`. | **Partial**; deletes correctly, returns HTTP 204 with no body. Work: small, align the documented response shape. |
| `POST /v1/responses/{response_id}/cancel` | Cancel a background response; return response state. | **Local**; cooperative cancellation; foreground cancellation rejected. The route ignores body options. |
| Delete/cancel: `response_id` | Target response identifier. | **Local**; 404 for absent state. |
| `GET /v1/responses/{response_id}/input_items` | Paginate the response's input history. | **Local**; `object=list`, `data`, `first_id`, `last_id`, `has_more`. |
| Input-items: `response_id`, `after`, `limit`, `order` | Target, cursor, page size, ascending/descending order. | **Local**; default 20, maximum 100, default descending. Extra `before` is local-only. |
| Input-items: `include` | Select optional item data. | **Partial**; ignored query option. Work: small. |
| `POST /v1/responses/input_tokens` | Count input tokens without generation. | **Partial**; actual template/vision count, but also runs generation-budget preparation. Work: small, separate counting from output-budget checks. |
| Count: `input`, `instructions`, `model`, `previous_response_id`, `parallel_tool_calls`, `reasoning`, `text`, `tool_choice`, `tools`, `truncation` | Describe the input and its model/template/tool configuration. | Same field behavior as create below; `object=response.input_tokens`, `input_tokens`. |
| Count: `conversation`, `personality` | Stored conversation; personality string (`friendly`, `pragmatic` or model-specific value). | **Off**; conversation requires a service; personality needs a model/template adapter. |
| `POST /v1/responses/compact` | Compact history and return an encrypted compaction item. | **Partial**; local model-generated, lossy summary; not OpenAI latent state or interchangeable ciphertext. Work: adapter, align item retention/compaction semantics. |
| Compact: `model`, `input`, `instructions`, `previous_response_id`, `prompt_cache_key` | Select model/history, summarization instructions and cache routing key. | **Local**; explicit model pass, encrypted local replay. Cache key is accepted only. Output: `id`, `object=response.compaction`, `created_at`, `output`, `usage`. |
| Compact: `prompt_cache_options.mode`, `.ttl`, `prompt_cache_retention`, `service_tier` | Cache policy and execution tier. | **Off** on this endpoint; engine cache policy or local tier validation required. |
| WebSocket `GET /v1/responses` / SDK `responses.connect` | Persistent bidirectional connection. | **Off**; no upgrade or SSE redirection. Work: adapter plus live request coordination; event matrix below. |
| `/v1/conversations` and `/{conversation_id}/items` | Create/retrieve/update/delete conversations; create/list/retrieve/delete items. | **Off**; separate durable state service, no redirect to `previous_response_id`. |
| Conversations: `conversation_id`, `item_id`, `items`, `metadata`, `include`, `after`, `limit`, `order` | Identifiers, item content, metadata, optional data and pagination. | **Off** with that resource; shared item shapes are catalogued below. |
| Conversation result: `id`, `created_at`, `metadata`, `object`; deletion: `id`, `deleted`, `object`; item list: `data`, `first_id`, `last_id`, `has_more`, `object` | Conversation identity/state, deletion acknowledgement and paginated items. | **Off** with Conversations; ordinary Responses input-item pagination is separate. |
| Auth, transport and errors | HTTP JSON, SSE; configured credentials; structured errors. | **Local**; optional server key via Bearer or `x-api-key`; 8 MiB body limit; no OpenAI organization/project/billing service. Invalid JSON/duplicate keys/non-finite numbers rejected. |

## Create request

Object subfields below cover the parent object as well. Missing optional settings generally use local defaults; null handling is field-specific (notably truncation and service_tier do not treat explicit null as omission).

| Feature / fields | Official meaning | Strata implementation; remaining work |
| --- | --- | --- |
| `access_programs`, `access_programs.cyber` | Domain access selection; `cyber=standard/daybreak_blue/daybreak_red`. | **Off**; OpenAI entitlement service; no local redirection. |
| `background` | Generate asynchronously; poll, replay or cancel. | **Local**; default false; requires `store=true`; bounded concurrent tasks. |
| `context_management`, `context_management.type`, `context_management.compact_threshold` | Automatic compaction entries: `type=compaction`, `compact_threshold` token threshold. | **Off**; explicit `/compact` exists, no automatic call. Work: adapter plus context-budget policy. |
| `conversation` | Conversation ID or object with `id`; automatically append input/output. | **Off**; Conversations service required. |
| `include` | Choose optional response data; values listed below. | **Partial**; accepts only encrypted reasoning and image-URL inclusion; selection semantics differ. |
| `input` | String or typed input-item list. | **Local**; text, vision and supported item replay; variants below. |
| `instructions` | System/developer instructions for this request. | **Local**; string only; previous response instructions are not inherited. |
| `max_output_tokens` | Generated-token ceiling including reasoning. | **Partial**; engine ceiling for main pass; an extra summary pass has a separate budget. Local minimum 1. Work: small total-budget alignment. |
| `max_tool_calls` | Limit built-in tool invocations across a response. | **Off**; no built-in execution loop. Add only alongside such a service; not a function-call count. |
| `metadata` | Up to 16 string pairs; 64-character keys, 512-character values. | **Local**; validates and stores/echoes; no platform search/dashboard. |
| `model` | Model identifier. | `=` configured engine model; exact local ID required, unknown ID returns 404; not the OpenAI model catalog. |
| `moderation`, `moderation.model`, `moderation.policy`, `moderation.policy.input`, `moderation.policy.input.mode`, `moderation.policy.output`, `moderation.policy.output.mode` | `model`; input/output `policy.*.mode=score/block`. | **Off**; separate moderation service, no fabricated scores or automatic local replacement. |
| `parallel_tool_calls` | Allow multiple tool calls in one response. | **Local**; default true; validation enforces permitted call multiplicity; client controls actual execution. |
| `previous_response_id` | Continue a stored response; independent of Conversations. | **Local**; only completed/incomplete prior responses; 409 for unavailable state; storage limits below. |
| `prompt`, `prompt.id`, `prompt.variables`, `prompt.version` | Cloud prompt `id`, `version` and typed `variables` (text/image/file). | **Off**; cloud prompt registry. Local `instructions`/`input` are explicit alternatives; no ID resolution. |
| `prompt_cache_key` | Route related prompts to reusable cache entries. | **Partial**; string accepted/echoed, no key-based cache routing. Work: engine/cache adapter. |
| `prompt_cache_options`, `prompt_cache_options.comparison_response_id`, `prompt_cache_options.mode`, `prompt_cache_options.prewarm`, `prompt_cache_options.ttl` | `mode=implicit/explicit`, `ttl=30m`, `prewarm`, `comparison_response_id` for cache behavior/diagnostics. | **Off**; engine/cache subsystem required; not inherently an OpenAI-only concept. |
| `prompt_cache_retention` | Retention policy `in_memory` or `24h`. | **Off**; engine cache-lifetime capability required; no advertised tier guarantees. |
| `reasoning`, `reasoning.context`, `reasoning.effort`, `reasoning.generate_summary`, `reasoning.mode`, `reasoning.summary` | Reasoning generation, scope and summaries. | **Local/Partial**; individual settings below. |
| `safety_identifier` | Stable end-user identifier for abuse monitoring; documented maximum 64 characters. | **Partial**; string accepted/echoed; no monitoring, maximum not enforced. Work: small validation; safety service separate. |
| `service_tier` | Execution tier `auto/default/fast/flex/priority`. | **Partial**; only `auto/default`, both reported as `default`; paid tiers off. Work: small for explicit local capability policy. |
| `store` | Retain response state for later operations. | **Local**; default true; process-local bounded TTL store, not durable cloud storage. |
| `stream` | Emit SSE instead of a final JSON object. | **Local**; default false; lifecycle/item/content events; buffering rules below. |
| `stream_options`, `stream_options.include_obfuscation` | `include_obfuscation` adds random padding to streamed events. | **Partial**; false/null accepted; true rejected. Work: small protocol padding, optional. |
| `temperature` | Sampling randomness, range 0–2. | `=` sampling when specified; local response default 0 rather than official default 1. |
| `text`, `text.format`, `text.verbosity` | Output format and requested verbosity. | **Local/Partial**; settings below. |
| `tool_choice` | Automatic, disabled, required, restricted or named tool selection. | **Local/Partial**; choices below. |
| `tools` | Available tool definitions. | **Local/Off** by type; client-owned tools below; no implicit server execution. |
| `top_logprobs` | Number of most likely alternatives per output token, 0–20. | **Off**; engine log-probability output plus wire accounting required. |
| `top_p` | Nucleus-sampling probability. | `=` existing sampling, range 0–1; local default 1. |
| `truncation` | `auto` drops excess input; `disabled` fails on oversized context. | **Partial**; only `disabled`; oversized input errors. Work: adapter with deliberate history/item truncation. |
| `user` | Deprecated end-user identifier; replaced by safety/cache identifiers. | **Partial**; string accepted/echoed, no downstream service. |
| `reasoning.effort` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. | **Partial**; none/minimal disable thinking; low/medium map directly; high/xhigh/max map to local xhigh. Model-dependent, not equivalent reasoning-quality levels. |
| `reasoning.context` | `auto`, `current_turn`, `all_turns`: which reasoning history remains visible. | **Local**; auto resolves to all_turns; current_turn preserves the active user/tool round; effective value reported. |
| `reasoning.mode` | Model-specific mode; standard/pro are documented examples. | **Partial**; only standard. Other modes require model capability; no pro emulation. |
| `reasoning.summary`, `reasoning.generate_summary` | auto/concise/detailed summaries; generate_summary is deprecated. | **Local**; extra model pass (256/512-token budget), included in usage; alias conflicts rejected. Not a hosted summarizer. |
| `text.verbosity` | Requested low/medium/high output verbosity. | **Partial**; prompt instruction; no dedicated engine control. |
| `text.format.type=text`: `type` | Ordinary text output. | `=` ordinary engine text. |
| `text.format.type=json_object`: `type` | Return valid JSON. | **Partial**; prompt plus post-generation parsing; no constrained decoder. Work: engine. |
| `text.format.type=json_schema`: `name`, `schema`, `type`, `description`, `strict` | Named JSON Schema; description and strictness control structured output. | **Partial**; Draft 2020-12 post-validation; local references only, no `$id`/external refs. `strict=false` still validates; description is validated but not prompted. Work: adapter alignment and engine constrained decoding. |

### Include values

| Feature / fields | Official meaning | Strata implementation; remaining work |
| --- | --- | --- |
| `reasoning.encrypted_content` | Encrypted reasoning for stateless replay. | **Partial**; accepted, but local encrypted content is emitted independently of include; keys/ciphertext are not OpenAI-compatible. |
| `message.input_image.image_url` | Expose input image URLs. | **Partial**; accepted; URLs remain in stored input items, not conditional output expansion. |
| `message.output_text.logprobs` | Expose output-token probability details. | **Off**; engine capability required. |
| `file_search_call.results` | Expose file-search matches. | **Off**; hosted retrieval off; explicit local retrieval tools possible. |
| `web_search_call.results` | Expose web-search results. | **Off**; hosted search off; explicit local client tools possible. |
| `web_search_call.action.sources` | Expose web-search source references. | **Off** with native search. |
| `computer_call_output.output.image_url` | Expose native computer screenshot URLs. | **Off** with native computer protocol; client function/custom image results work. |
| `code_interpreter_call.outputs` | Expose code-interpreter results. | **Off**; hosted execution off; explicit client code execution possible. |

### Tool selection

| Feature / fields | Official meaning | Strata implementation; remaining work |
| --- | --- | --- |
| `none`, `auto`, `required` | Disable calls, allow model choice or require a call. | **Local**; generated output validated against the selection. |
| `allowed_tools`: `mode`, `tools`, `type` | Restrict available tool identities; mode auto/required. | **Local** for supported client tools; named/namespace identities preserved. |
| `function` / `custom` choice: `name`, `type` | Force a named function/custom tool. | **Local**; local optional `namespace` also supported. |
| Native built-in choice: `type` | Select file_search, web_search_preview, computer, computer_use_preview, computer_use, dated web_search_preview, image_generation or code_interpreter. | **Off** with those native tools; no automatic replacement. |
| `mcp` choice: `server_label`, `type`, `name` | Select a server, optionally a tool name. | **Off**; explicit client MCP function tools are a different contract. |
| `programmatic_tool_calling` choice: `type` | Select programmatic tool orchestration. | **Off**; separate execution/orchestration service. |
| `apply_patch` / `shell` choice: `type` | Select native patch or shell calls. | **Off**; adapter work possible for client execution, no automatic translation. |

## Tool definitions and execution environments

| Feature / fields | Official meaning | Strata implementation; remaining work |
| --- | --- | --- |
| `function`: `name`, `parameters`, `strict`, `type`, `allowed_callers`, `async`, `defer_loading`, `description`, `output_schema` | Named function: JSON Schema arguments, description, strictness, deferred loading, permitted callers, async flag and output schema. | **Local/Partial**; arguments parsed/validated, strict defaults true; false disables schema validation. `allowed_callers` only direct; `async` and `output_schema` rejected. Strictness is post-validation, not constrained decoding; Work: engine for generation guarantees. |
| `custom`: `name`, `type`, `allowed_callers`, `async`, `defer_loading`, `description`, `format` | Named raw-text tool with optional format, description, deferred loading, permitted callers and async flag. | **Local/Partial**; internally adapted through a string argument, emits raw input; direct callers only; async rejected. |
| Custom format `text`: `type` | Unconstrained tool-input text. | **Local**. |
| Custom format `grammar`: `definition`, `syntax`, `type` | Definition with syntax lark/regex. | **Partial**; post-validation, Lark LALR with bundled common imports; 64 KiB definition limit and regex timeout. Work: engine for decoder constraints. |
| `namespace`: `description`, `name`, `tools`, `type` | Name, description and contained tool definitions. | **Local**; one level, function/custom children; no nested namespaces; namespace description included once. |
| `tool_search`: `type`, `description`, `execution`, `parameters` | Search definitions with execution server/client, description and argument schema. | **Partial**; client execution only; deferred definitions load from matching tool_search_output. Server search off; work: service. |
| `file_search`: `type`, `vector_store_ids`, `filters`, `max_num_results`, `ranking_options`, `ranking_options.hybrid_search`, `ranking_options.hybrid_search.embedding_weight`, `ranking_options.hybrid_search.text_weight`, `ranking_options.ranker`, `ranking_options.score_threshold` | Hosted vector-store retrieval; filters, result limit, ranker, score threshold and hybrid embedding/text weights. | **Off**; OpenAI vector-store service off; local client retrieval is an explicit alternative. Native local retrieval requires a service adapter. |
| Retrieval comparison filter: `key`, `type`, `value` | Key/value comparison: eq/ne/gt/gte/lt/lte/in/nin. | **Off** with native file search. |
| Retrieval compound filter: `filters`, `type` | Recursive and/or filter list. | **Off** with native file search. |
| `computer`: `type` | Native computer-action tool. | **Off**; protocol adapter plus model action grounding needed. Client function/custom automation is usable now; no redirection. |
| `computer_use_preview`: `display_height`, `display_width`, `environment`, `type` | Display width/height and environment for native computer actions. | **Off**; same adapter/model requirements; preview compatibility separate from execution service. |
| `web_search`, `web_search_2025_08_26`: `type`, `external_web_access`, `filters`, `filters.allowed_domains`, `search_context_size`, `user_location`, `user_location.city`, `user_location.country`, `user_location.region`, `user_location.timezone`, `user_location.type` | Search context size, external-web access, allowed domains and approximate user location. | **Off**; OpenAI search service off; client search tools are an explicit alternative. |
| `web_search_preview`, `web_search_preview_2025_03_11`: `type`, `search_content_types`, `search_context_size`, `user_location`, `user_location.type`, `user_location.city`, `user_location.country`, `user_location.region`, `user_location.timezone` | Preview search, text/image content selection, context size and approximate location (city/country/region/timezone). | **Off**; no native search provider or redirect. |
| `mcp`: `server_label`, `type`, `allowed_callers`, `allowed_tools`, `authorization`, `connector_id`, `defer_loading`, `headers`, `require_approval`, `server_description`, `server_url`, `tunnel_id` | Provider connects to a remote server/tunnel or cloud connector; headers/auth, labels/descriptions, filters, approvals, callers and deferred loading. | **Off**; server-owned remote MCP executor required. Client-connected MCP exported as function/custom tools works; connector IDs and tunnels have no local resolution. |
| MCP allowed-tools filter: `read_only`, `tool_names` | Tool-name and read-only restrictions; alternatively an array of names. | **Off** with provider-owned MCP. |
| MCP approval filter: `always`, `always.read_only`, `always.tool_names`, `never`, `never.read_only`, `never.tool_names` | Always/never approval by name/read-only; alternatively always/never for all tools. | **Off** with provider-owned MCP; client approvals remain client-owned. |
| `code_interpreter`: `container`, `type`, `allowed_callers` | Hosted container ID or automatic container configuration; permitted callers. | **Off**; cloud container service disabled; explicit client code tools possible, no container-ID mapping. |
| Code-interpreter automatic container: `type`, `file_ids`, `memory_limit`, `network_policy` | Files, memory limit and network policy. | **Off**; an isolated local executor would be a separate service, not an engine parameter. |
| `programmatic_tool_calling`: `type` | Run generated programs that invoke tools. | **Off**; separate orchestration/execution service; no hidden execution. |
| `image_generation`: `type`, `action`, `background`, `input_fidelity`, `input_image_mask`, `input_image_mask.file_id`, `input_image_mask.image_url`, `model`, `moderation`, `output_compression`, `output_format`, `partial_images`, `quality`, `size` | Generation/edit action, model, image mask, fidelity, transparency, moderation, compression, format, partial images, quality and size. | **Off**; OpenAI image service disabled. An explicit client image tool can return images; no automatic native dispatch. |
| `local_shell`: `type` | Legacy native client-side shell tool. | **Off**; adapter possible; generic client function/custom shell tools remain usable. |
| `shell`: `type`, `allowed_callers`, `environment` | Native shell; permitted callers and local/automatic/referenced container environment. | **Off**; local environment needs a protocol adapter; hosted containers require a separate service. |
| `apply_patch`: `type`, `allowed_callers` | Native filesystem patch tool and permitted callers. | **Off**; client-side protocol adapter possible; function/custom patch tools work explicitly. |
| Shell `container_auto`: `type`, `file_ids`, `memory_limit`, `network_policy`, `skills` | Files, memory allocation, network policy and skills. | **Off**; hosted container execution off; isolated local execution would require a service. |
| `container_reference`: `container_id`, `type` | Existing container ID. | **Off**; no hosted/local container registry or ID redirection. |
| `local` environment: `type`, `skills`, `skills.description`, `skills.name`, `skills.path` | Client-local execution, optionally skills. | **Off** in native shell protocol; adapter work, not intrinsically a cloud-only feature. |
| Network `disabled`: `type` | No container network access. | **Off** with containers; execution service must enforce policy. |
| Network `allowlist`: `allowed_domains`, `type`, `domain_secrets`, `domain_secrets.domain`, `domain_secrets.name`, `domain_secrets.value` | Allowed domains and scoped secrets with domain/name/value. | **Off** with containers; separate network enforcement required. |
| `skill_reference`: `skill_id`, `type`, `version` | Hosted skill ID and version. | **Off**; OpenAI skill registry disabled, no local ID mapping. |
| `inline` skill: `description`, `name`, `source`, `source.data`, `source.media_type`, `source.type`, `type` | Description/name and base64 ZIP source (`data`, `media_type`, `type`). | **Off** with shell containers; local handling needs an execution/skill adapter. |
| Domain secret: `domain`, `name`, `value` | Domain-scoped secret name/value. | **Off** with container network policies. |
| Inline skill source: `data`, `media_type`, `type` | Base64 data with application/zip media type. | **Off** with inline skills. |
| Local skill: `description`, `name`, `path` | Description/name and filesystem path. | **Off** in native shell environment; client-owned skill handling is independent. |
| `defer_loading` | Keep a definition out of context until discovered. | **Local** for function/custom tools; requires a client tool_search definition. Other tool types are off. |
| `allowed_callers`, `caller` | Direct model calls vs program-originated calls. | **Partial**; direct only; programmatic caller rejected. Program execution requires a service. |

## Input, output and replay items

Each row lists every field of that shape, including nested fields. Shared `type` discriminates the variant; `id` identifies an item, `call_id` joins a call to its result, `status` describes its lifecycle and `created_by` identifies its producer. Input/output schema aliases are combined. Accepted history metadata is not necessarily an engine input.

| Feature / fields | Official meaning | Strata implementation; remaining work |
| --- | --- | --- |
| `message`: `content`, `role`, `phase`, `type`, `status`, `id` | Role user/assistant/system/developer; string or part content; item ID/status; assistant phase commentary/final_answer. | **Local/Partial**; role/content rendered; input phase saved but not rendered or emitted; input status/ID preserved as history. Work: adapter for meaningful phase output. |
| `input_text`: `text`, `type`, `prompt_cache_breakpoint`, `prompt_cache_breakpoint.mode` | Text content; explicit prompt-cache breakpoint and mode. | **Partial**; text mapped directly; prompt_cache_breakpoint/mode ignored. Work: engine cache controls or explicit rejection. |
| `input_image`: `detail`, `type`, `file_id`, `image_url`, `prompt_cache_breakpoint`, `prompt_cache_breakpoint.mode` | URL/data image or platform file ID; detail auto/low/high/original; explicit cache breakpoint. | **Partial**; HTTP(S)/data image_url uses engine vision. file_id rejected; detail validated then discarded; cache breakpoint ignored. Work: engine vision-detail/cache adapter; file IDs need a file service. |
| `input_file`: `type`, `detail`, `file_data`, `file_id`, `file_url`, `filename`, `prompt_cache_breakpoint`, `prompt_cache_breakpoint.mode` | File data/ID/URL, filename, detail and cache breakpoint. | **Off**; ingestion/file service needed. File IDs have no local resolution; no automatic text conversion. |
| `output_text`: `annotations`, `text`, `type`, `logprobs`, `logprobs.token`, `logprobs.bytes`, `logprobs.logprob`, `logprobs.top_logprobs`, `logprobs.top_logprobs.token`, `logprobs.top_logprobs.bytes`, `logprobs.top_logprobs.logprob` | Text with annotations and token/byte/logprob/top_logprobs records. | **Partial**; text works; generated annotations/logprobs empty, replay annotations ignored. Engine logprobs and annotation-producing tools required. |
| `file_citation`: `file_id`, `filename`, `index`, `type` | File ID/name and citation index. | **Off** for generation; ignored during text replay; file retrieval/registry required. |
| `url_citation`: `end_index`, `start_index`, `title`, `type`, `url` | Source URL/title and start/end text offsets. | **Off** for generation; ignored during text replay; retrieval annotation adapter required. |
| `container_file_citation`: `container_id`, `end_index`, `file_id`, `filename`, `start_index`, `type` | Container/file ID/name and start/end text offsets. | **Off**; hosted execution/files disabled; ignored during text replay. |
| `file_path`: `file_id`, `index`, `type` | Generated file reference and position. | **Off**; no file registry; ignored during text replay. |
| `refusal`: `refusal`, `type` | Structured refusal text. | **Partial**; replayed as plain text, never emitted as a refusal item. Work: model refusal signal and protocol adapter. |
| `function_call`: `arguments`, `call_id`, `name`, `type`, `id`, `async`, `caller`, `namespace`, `status` | Named JSON argument call; optional namespace, caller, async flag and lifecycle metadata. | **Local/Partial**; JSON object arguments; matching names/IDs enforced; direct caller only. Replay async flag ignored, not generated; status stored. Work: explicit async validation/semantics. |
| `function_call_output`: `output`, `type`, `id`, `call_id`, `caller`, `name`, `namespace`, `status`, `created_by` | Matching call result as string or text/image/file parts; optional caller/name/namespace/producer/status. | **Local/Partial**; requires matching unanswered call_id/type; text/image supported, files off; direct caller; extra producer metadata not interpreted. |
| `custom_tool_call`: `call_id`, `input`, `name`, `type`, `id`, `async`, `caller`, `namespace` | Named raw-input call, optionally namespace/caller/async. | **Local/Partial**; raw text and namespace preserved; direct caller only; async ignored on replay, not emitted. |
| `custom_tool_call_output`: `call_id`, `output`, `type`, `id`, `caller`, `status`, `created_by` | Matching raw-tool result and lifecycle/producer metadata. | **Local/Partial**; matching call_id/type, text/image results; file parts off; producer metadata not interpreted. |
| Call-item result metadata: `id`, `status`, `created_by` | Server-assigned ID/status and optional producer added to call shapes. | **Partial**; IDs/status generated; created_by not emitted/interpreted. |
| Caller `direct`: `type` | Call originates directly from the model. | **Local** on supported client function/custom calls; native shell/patch remain off. |
| Caller `program`: `caller_id`, `type` | Caller program identified by caller_id. | **Off**; program execution/orchestration required. |
| `tool_search_call`: `arguments`, `type`, `id`, `call_id`, `execution`, `status`, `created_by` | Discovery arguments, execution client/server and optional call/producer ID. | **Partial**; client call/ID emitted, argument object validated; server execution off; producer metadata not interpreted. |
| `tool_search_output`: `tools`, `type`, `id`, `call_id`, `execution`, `status`, `created_by` | Discovered tool definitions and execution/call/lifecycle metadata. | **Partial**; client-only matching replay loads supported definitions; server output rejected; created_by not interpreted. |
| `additional_tools`: `role`, `tools`, `type`, `id` | Developer-role capability declarations containing tool definitions. | **Local**; preserved across compaction; definitions follow supported-tool restrictions. |
| `configuration_update`: `type`, `id`, `reasoning`, `reasoning.effort` | History item updates reasoning effort. | **Off**; request reasoning settings are available, no mid-history configuration update. Work: adapter. |
| `reasoning`: `id`, `summary`, `summary.text`, `summary.type`, `type`, `content`, `content.text`, `content.type`, `encrypted_content`, `status` | Reasoning text, summary_text parts, encrypted content and status. | **Local/Partial**; plaintext and local encrypted replay; summary-only replay is stored but not rendered; not cross-provider ciphertext. Reasoning needs a capable model/template. |
| `compaction`: `encrypted_content`, `type`, `id`, `created_by` | Encrypted compacted context with optional producer metadata. | **Partial**; local encrypted model summary replaces preceding history; capability declarations retained; created_by unused; no OpenAI latent-state compatibility. |
| `compaction_trigger`: `type` | Explicit input item triggers in-stream compaction. | **Off**; `/compact` available explicitly; automatic/in-stream policy needs adapter work. |
| `item_reference`: `id`, `type` | Reference a prior stored item by ID. | **Off**; item-resolution adapter required; explicit full-item replay works. |
| `program`: `id`, `call_id`, `code`, `fingerprint`, `type` | Generated program code, fingerprint and call identifier. | **Off**; separate program execution service. |
| `program_output`: `id`, `call_id`, `result`, `status`, `type` | Program result and completion status. | **Off** with program execution. |
| `file_search_call`: `id`, `queries`, `status`, `type`, `results`, `results.attributes`, `results.file_id`, `results.filename`, `results.score`, `results.text` | Queries, search state and matches (attributes/file ID/name/score/text). | **Off**; hosted vector retrieval disabled; client retrieval returns ordinary tool output. |
| `web_search_call`: `id`, `action`, `status`, `type` | Search/open/find action and search lifecycle. | **Off**; hosted search disabled; no native local provider. |
| Web action `search`: `type`, `queries`, `query`, `sources`, `sources.type`, `sources.url` | Query/queries and source URL/type records. | **Off** with native web search. |
| Web action `open_page`: `type`, `url` | Open a URL. | **Off** with native web search. |
| Web action `find_in_page`: `pattern`, `type`, `url` | Find a pattern in a URL. | **Off** with native web search. |
| `computer_call`: `id`, `call_id`, `pending_safety_checks`, `pending_safety_checks.id`, `pending_safety_checks.code`, `pending_safety_checks.message`, `status`, `type`, `action`, `actions` | One action or action batch; pending checks with ID/code/message. | **Off**; client-native action adapter and model grounding needed; no server computer control. |
| `computer_call_output`: `call_id`, `output`, `output.type`, `output.file_id`, `output.image_url`, `type`, `id`, `acknowledged_safety_checks`, `acknowledged_safety_checks.id`, `acknowledged_safety_checks.code`, `acknowledged_safety_checks.message`, `status`, `created_by` | Screenshot result; acknowledged checks with ID/code/message, status and producer. | **Off**; explicit client function/custom screenshot results work as input_image instead. |
| Computer action `click`: `button`, `type`, `x`, `y`, `keys` | Click at x/y with a button and modifier keys. | **Off** with native computer protocol; adapter and suitable model required. |
| Computer action `double_click`: `keys`, `type`, `x`, `y` | Double-click at x/y with modifier keys. | **Off** with native computer protocol; adapter and suitable model required. |
| Computer action `drag`: `path`, `path.x`, `path.y`, `type`, `keys` | Drag through path points (x/y). | **Off** with native computer protocol; adapter and suitable model required. |
| Computer action `keypress`: `keys`, `type` | Press a key sequence. | **Off** with native computer protocol; adapter and suitable model required. |
| Computer action `move`: `type`, `x`, `y`, `keys` | Move pointer to x/y. | **Off** with native computer protocol; adapter and suitable model required. |
| Computer action `screenshot`: `type` | Capture screen. | **Off** with native computer protocol; adapter and suitable model required. |
| Computer action `scroll`: `scroll_x`, `scroll_y`, `type`, `x`, `y`, `keys` | Scroll at x/y by scroll_x/scroll_y. | **Off** with native computer protocol; adapter and suitable model required. |
| Computer action `type`: `text`, `type` | Enter text. | **Off** with native computer protocol; adapter and suitable model required. |
| Computer action `wait`: `type` | Wait before continuing. | **Off** with native computer protocol; adapter and suitable model required. |
| `image_generation_call`: `id`, `result`, `status`, `type`, `action`, `background`, `output_format`, `quality`, `revised_prompt`, `size` | Generated result and status, action, background, output format, quality, revised prompt and size. | **Off**; hosted image generation disabled; explicit client image results are separate. |
| `code_interpreter_call`: `id`, `code`, `container_id`, `outputs`, `status`, `type` | Code, container ID, outputs and execution status. | **Off**; hosted code interpreter disabled. |
| Interpreter output `logs`: `logs`, `type` | Execution log text. | **Off** with native code interpreter. |
| Interpreter output `image`: `type`, `url` | Generated image URL. | **Off** with native code interpreter. |
| `local_shell_call`: `id`, `action`, `action.command`, `action.env`, `action.type`, `action.timeout_ms`, `action.user`, `action.working_directory`, `call_id`, `status`, `type` | Exec action: command, environment variables, timeout, user and working directory. | **Off**; native client-side adapter possible; not an OpenAI hosted-service dependency. |
| `local_shell_call_output`: `id`, `output`, `type`, `status` | Client execution output and status. | **Off** with native legacy shell protocol. |
| `shell_call`: `action`, `action.commands`, `action.max_output_length`, `action.timeout_ms`, `call_id`, `type`, `id`, `caller`, `environment`, `status`, `created_by` | Command batch, timeout/output limit, environment, caller and producer metadata. | **Off**; client-local adapter possible; hosted containers remain off. |
| `shell_call_output`: `call_id`, `output`, `output.outcome`, `output.stderr`, `output.stdout`, `type`, `id`, `caller`, `max_output_length`, `status`, `output.created_by`, `created_by` | Per-command stdout/stderr/outcome, output limit, status, caller and producer metadata. | **Off** with native shell protocol; explicit client text outputs work. |
| Shell outcome `timeout`: `type` | Command timed out. | **Off** with native shell protocol. |
| Shell outcome `exit`: `exit_code`, `type` | Command exit code. | **Off** with native shell protocol. |
| `apply_patch_call`: `call_id`, `operation`, `status`, `type`, `id`, `caller`, `created_by` | Patch operation, matching call ID, status, caller and producer. | **Off**; client-local adapter possible, no hosted execution required. |
| Patch `create_file`: `diff`, `path`, `type` | Create path using diff. | **Off** with native patch protocol. |
| Patch `delete_file`: `path`, `type` | Delete path. | **Off** with native patch protocol. |
| Patch `update_file`: `diff`, `path`, `type` | Update path using diff. | **Off** with native patch protocol. |
| `apply_patch_call_output`: `call_id`, `status`, `type`, `id`, `caller`, `output`, `created_by` | Completed/failed patch result with optional output/caller/producer. | **Off** with native patch protocol. |
| `mcp_list_tools`: `id`, `server_label`, `tools`, `tools.input_schema`, `tools.name`, `tools.annotations`, `tools.description`, `type`, `error` | Server tool discovery: name/input_schema/annotations/description; discovery error. | **Off**; provider-owned MCP executor disabled; client discovery uses ordinary function definitions. |
| `mcp_approval_request`: `id`, `arguments`, `name`, `server_label`, `type` | Request approval for server/tool arguments. | **Off** with provider-owned MCP; client approval UI remains client-owned. |
| `mcp_approval_response`: `approval_request_id`, `approve`, `type`, `id`, `reason` | Approve/reject a matching request with optional reason. | **Off** with provider-owned MCP. |
| `mcp_call`: `id`, `arguments`, `name`, `server_label`, `type`, `approval_request_id`, `error`, `output`, `status` | Remote server/tool invocation, approval link, result/error and lifecycle. | **Off**; no implicit provider connection. |
| MCP protocol error: `code`, `message`, `type` | Protocol code/message. | **Off** with provider-owned MCP. |
| MCP execution error: `content`, `type` | Tool error content. | **Off** with provider-owned MCP. |
| MCP HTTP error: `code`, `message`, `type` | Remote HTTP code/message. | **Off** with provider-owned MCP. |
| `input_audio`: `input_audio`, `input_audio.data`, `input_audio.format`, `type` | Base64 audio data and format. | **Off**; engine audio encoder and content adapter required; not inherently cloud-only. |
| `output_audio`: `data`, `transcript`, `type` | Generated base64 audio and transcript. | **Off**; engine audio decoder and streaming adapter required. |
| Video input | Typed video content with frame/timestamp information where advertised by a model guide; no stable video item in this reference snapshot. | **Off**; engine multimodal capability and an explicit wire contract required; no silent conversion. |
| `computer_screenshot`: `type`, `file_id`, `image_url` | Native screenshot file ID or image URL. | **Off** with native computer protocol; file IDs have no local mapping. |
| Conversation-only `computer_screenshot`: `detail`, `file_id`, `image_url`, `type`, `prompt_cache_breakpoint`, `prompt_cache_breakpoint.mode`; `text` / `summary_text` / `reasoning_text`: `text`, `type` | Screenshot and text-part variants in stored conversation items. | **Off** with Conversations; screenshot/native-file and text-replay limits above still apply. |
| `reasoning_text` content: `text`, `type` | Plaintext reasoning part. | **Local**; model reasoning stream/replay. |
| Configuration-update stored item: `id`, `type`, `reasoning`, `reasoning.effort` | Assigned ID with reasoning effort update. | **Off** with configuration_update. |
| Stored input message: `id`, `content`, `role`, `type`, `status` | Message ID/content/role/type/status. | **Local** for supported content; status metadata does not change rendering. |
| Stored shell output content: `outcome`, `stderr`, `stdout` | Per-command stdout/stderr/outcome and producer. | **Off** with native shell protocol. |

## Response objects, accounting and state

| Feature / fields | Official meaning | Strata implementation; remaining work |
| --- | --- | --- |
| `id`, `created_at`, `object`, `completed_at` | Response identity/type and timestamps. | **Local**; completed_at also set for incomplete/cancelled/failed terminal states. |
| `status` | queued/in_progress/completed/failed/incomplete/cancelled. | **Local**; output items have their own statuses; no separate cancelled SSE event in the stable reference. |
| `error`, `error.code`, `error.message`, `error.misalignment`, `error.misalignment.detailed_explanation`, `error.misalignment.error_type`, `error.misalignment.steer`, `error.misalignment.steer.message` | Failure code/message and optional safety misalignment explanation/type/steer.message. | **Partial**; local code/message; no safety misalignment fields. Work: small for protocol shape, service/model for safety classification. |
| `incomplete_details`, `incomplete_details.reason` | Reason: max_output_tokens/max_messages/content_filter/steered. | **Partial**; local token-limit accounting; no content-filter/steering service or max-message policy. |
| `instructions`, `metadata`, `model`, `parallel_tool_calls`, `temperature`, `tool_choice`, `tools`, `top_p`, `background`, `max_output_tokens`, `previous_response_id`, `prompt_cache_key`, `reasoning`, `reasoning.context`, `reasoning.effort`, `reasoning.generate_summary`, `reasoning.mode`, `reasoning.summary`, `safety_identifier`, `service_tier`, `text`, `text.format`, `text.verbosity`, `truncation`, `user` | Effective request settings and tool definitions. | Same behavior as request rows; defaults normalized locally; engine configuration may supply sampling defaults. |
| `output` | Ordered generated message/reasoning/tool items. | **Local/Partial** by item type; types and limitations above. |
| `access_programs`, `access_programs.cyber`, `conversation`, `conversation.id`, `max_tool_calls`, `moderation`, `moderation.input`, `moderation.output`, `prompt`, `prompt.id`, `prompt.variables`, `prompt.version`, `prompt_cache_diagnostics`, `prompt_cache_options`, `prompt_cache_options.mode`, `prompt_cache_options.ttl`, `prompt_cache_options.comparison_response_id`, `prompt_cache_retention`, `top_logprobs` | Entitlements, conversation, execution limits, moderation, cloud prompt, cache diagnostics/policy and logprobs. | **Off**; not emitted as supported capabilities; nested shapes below where applicable. |
| `usage`, `usage.input_tokens`, `usage.input_tokens_details`, `usage.input_tokens_details.cache_write_tokens`, `usage.input_tokens_details.cached_tokens`, `usage.output_tokens`, `usage.output_tokens_details`, `usage.output_tokens_details.reasoning_tokens`, `usage.total_tokens` | Input/output/total token counts and token breakdown. | **Local/Partial**; includes summary pass; cached_tokens uses engine reuse counts; reasoning_tokens reported. cache_write_tokens always 0, not measured. Work: engine accounting. |
| Conversation reference: `id` | Stored conversation ID. | **Off**; separate Conversations resource required. |
| `moderation_result`: `categories`, `category_applied_input_types`, `category_scores`, `flagged`, `model`, `type` | Flag/categories, applied input types, category scores and moderation model. | **Off**; no fabricated local ratings or automatic provider replacement. |
| Moderation error: `code`, `message`, `type` | Moderation failure code/message/type. | **Off** with moderation service. |
| Cache diagnostic `cache_miss`: `cache_missed_tokens`, `reason`, `type`, `comparison_reusable_tokens` | Missed tokens/reason and reusable tokens relative to a comparison response. | **Off**; engine/cache instrumentation required. |
| Cache diagnostic `cache_hit`: `type` | Cache hit indicator. | **Off**; no diagnostic signal exposed. |
| `comparison_response_not_found`: `type` | Unavailable cache-comparison reference. | **Off** with cache diagnostics. |
| Cache diagnostic `unavailable`: `type` | Diagnostics cannot be provided. | **Off**; diagnostics omitted rather than synthesized. |
| Response error model: `code`, `message`, `misalignment`, `misalignment.detailed_explanation`, `misalignment.error_type`, `misalignment.steer`, `misalignment.steer.message` | Failure code/message and optional misalignment explanation/type/steering advice. | **Partial**; code/message only; local errors not a claim of OpenAI safety classification. |
| Usage shape: `input_tokens`, `input_tokens_details`, `input_tokens_details.cache_write_tokens`, `input_tokens_details.cached_tokens`, `output_tokens`, `output_tokens_details`, `output_tokens_details.reasoning_tokens`, `total_tokens` | Input tokens, cached/write counts, output/reasoning tokens and total. | Same accounting limits as usage above. |
| Result text configuration: `format`, `verbosity` | Selected format and verbosity. | Same format/verbosity limits as create. |
| Resolved prompt reference: `id`, `variables`, `version` | Prompt ID, typed variables and version. | **Off**; cloud prompt resource disabled. |
| Compaction response shape: `id`, `created_at`, `object`, `output`, `usage`, `usage.input_tokens`, `usage.input_tokens_details`, `usage.input_tokens_details.cache_write_tokens`, `usage.input_tokens_details.cached_tokens`, `usage.output_tokens`, `usage.output_tokens_details`, `usage.output_tokens_details.reasoning_tokens`, `usage.total_tokens` | ID, creation time, compaction output and usage. | **Partial**; local summary/ciphertext; accounting as above. |
| Input-item list shape: `data`, `first_id`, `has_more`, `last_id`, `object` | Data, pagination IDs, more-page flag and object type. | **Local**. |
| Token-count shape: `input_tokens`, `object` | Input-token count and object type. | **Partial**; counting endpoint preparation caveat above. |
| Local storage limits | Official store supports later retrieval and response chaining; provider retention is not an engine parameter. | **Local**; defaults 128 records, 64 MiB total, 16 MiB per record, 1-hour TTL, 4 active tasks. Eviction/restart loses IDs; no persistent Conversations service. |
| Encrypted replay | Opaque reasoning/compaction content can be carried by the client. | **Local**; authenticated encryption bound to local model/key. A configured stable key preserves ciphertext replay across restart, not stored IDs; no OpenAI ciphertext compatibility. |
| HTTP error envelope: `error.message`, `.type`, `.param`, `.code` | Describe validation/server failure and identify the offending parameter. | **Local**; 400 validation, 404 unknown resource/model, 409 state conflict, 413 oversized body; generation failure becomes response.failed after creation. No successful fabricated unsupported result. |

## SSE events

All events carry `type` and `sequence_number`. `response`, `item`, `part`, annotations and errors reuse the shapes above; indices select their positions. Each event below lists its additional payload fields. Ordinary text/reasoning stream incrementally; tool calls, structured output and requested summaries are buffered until complete validation. No token probabilities or event obfuscation are generated.

| Feature / fields | Official meaning | Strata implementation; remaining work |
| --- | --- | --- |
| `response.created`: `response` | Created. | **Local**. |
| `response.in_progress`: `response` | In progress. | **Local**. |
| `response.completed`: `response` | Completed. | **Local**. |
| `response.failed`: `response` | Failed. | **Local**. |
| `response.incomplete`: `response` | Incomplete. | **Local**. |
| `response.output_item.added`: `item`, `output_index` | Output item / added. | **Local**. |
| `response.output_item.done`: `item`, `output_index` | Output item / done. | **Local**. |
| `response.content_part.added`: `content_index`, `item_id`, `output_index`, `part` | Content part / added. | **Local**. |
| `response.content_part.done`: `content_index`, `item_id`, `output_index`, `part` | Content part / done. | **Local**. |
| `response.output_text.delta`: `content_index`, `delta`, `item_id`, `logprobs`, `logprobs.token`, `logprobs.logprob`, `logprobs.top_logprobs`, `logprobs.top_logprobs.token`, `logprobs.top_logprobs.logprob`, `output_index` | Output text / delta. | **Partial**; logprobs empty. |
| `response.output_text.done`: `content_index`, `item_id`, `logprobs`, `logprobs.token`, `logprobs.logprob`, `logprobs.top_logprobs`, `logprobs.top_logprobs.token`, `logprobs.top_logprobs.logprob`, `output_index`, `text` | Output text / done. | **Partial**; logprobs empty. |
| `response.refusal.delta`: `content_index`, `delta`, `item_id`, `output_index` | Refusal / delta. | **Off**; refusal signal/model plus adapter required. |
| `response.refusal.done`: `content_index`, `item_id`, `output_index`, `refusal` | Refusal / done. | **Off**; refusal signal/model plus adapter required. |
| `response.function_call_arguments.delta`: `delta`, `item_id`, `output_index` | Function call arguments / delta. | **Local**; validated whole call is buffered before publication. |
| `response.function_call_arguments.done`: `arguments`, `item_id`, `output_index` | Function call arguments / done. | **Local**; validated whole call is buffered before publication. |
| `response.file_search_call.in_progress`: `item_id`, `output_index` | File search call / in progress. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.file_search_call.searching`: `item_id`, `output_index` | File search call / searching. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.file_search_call.completed`: `item_id`, `output_index` | File search call / completed. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.web_search_call.in_progress`: `item_id`, `output_index` | Web search call / in progress. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.web_search_call.searching`: `item_id`, `output_index` | Web search call / searching. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.web_search_call.completed`: `item_id`, `output_index` | Web search call / completed. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.reasoning_summary_part.added`: `item_id`, `output_index`, `part`, `part.text`, `part.type`, `summary_index` | Reasoning summary part / added. | **Local**; generated by an extra local pass. |
| `response.reasoning_summary_part.done`: `item_id`, `output_index`, `part`, `part.text`, `part.type`, `summary_index`, `status` | Reasoning summary part / done. | **Local**; generated by an extra local pass; optional incomplete status not generated. |
| `response.reasoning_summary_text.delta`: `delta`, `item_id`, `output_index`, `summary_index` | Reasoning summary text / delta. | **Local**; generated by an extra local pass. |
| `response.reasoning_summary_text.done`: `item_id`, `output_index`, `summary_index`, `text` | Reasoning summary text / done. | **Local**; generated by an extra local pass. |
| `response.reasoning_text.delta`: `content_index`, `delta`, `item_id`, `output_index` | Reasoning text / delta. | **Local**. |
| `response.reasoning_text.done`: `content_index`, `item_id`, `output_index`, `text` | Reasoning text / done. | **Local**. |
| `response.image_generation_call.completed`: `item_id`, `output_index` | Image generation call / completed. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.image_generation_call.generating`: `item_id`, `output_index` | Image generation call / generating. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.image_generation_call.in_progress`: `item_id`, `output_index` | Image generation call / in progress. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.image_generation_call.partial_image`: `item_id`, `output_index`, `partial_image_b64`, `partial_image_index`, `background`, `output_format`, `quality`, `size` | Image generation call / partial image. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.mcp_call_arguments.delta`: `delta`, `item_id`, `output_index` | Mcp call arguments / delta. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.mcp_call_arguments.done`: `arguments`, `item_id`, `output_index` | Mcp call arguments / done. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.mcp_call.completed`: `item_id`, `output_index` | Mcp call / completed. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.mcp_call.failed`: `item_id`, `output_index` | Mcp call / failed. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.mcp_call.in_progress`: `item_id`, `output_index` | Mcp call / in progress. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.mcp_list_tools.completed`: `item_id`, `output_index` | Mcp list tools / completed. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.mcp_list_tools.failed`: `item_id`, `output_index` | Mcp list tools / failed. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.mcp_list_tools.in_progress`: `item_id`, `output_index` | Mcp list tools / in progress. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.code_interpreter_call.in_progress`: `item_id`, `output_index` | Code interpreter call / in progress. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.code_interpreter_call.interpreting`: `item_id`, `output_index` | Code interpreter call / interpreting. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.code_interpreter_call.completed`: `item_id`, `output_index` | Code interpreter call / completed. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.code_interpreter_call_code.delta`: `delta`, `item_id`, `output_index` | Code interpreter call code / delta. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.code_interpreter_call_code.done`: `code`, `item_id`, `output_index` | Code interpreter call code / done. | **Off** with corresponding native hosted/provider tool; service/adapter required. |
| `response.output_text.annotation.added`: `annotation`, `annotation_index`, `content_index`, `item_id`, `output_index` | Output text / annotation / added. | **Off**; annotation-producing adapter/tool required. |
| `response.queued`: `response` | Queued. | **Local**. |
| `response.custom_tool_call_input.delta`: `delta`, `item_id`, `output_index` | Custom tool call input / delta. | **Local**; validated whole call is buffered before publication. |
| `response.custom_tool_call_input.done`: `input`, `item_id`, `output_index` | Custom tool call input / done. | **Local**; validated whole call is buffered before publication. |
| `error`: `code`, `message`, `param` | Error. | **Off** as standalone SSE error; local failures use response.failed / HTTP error; work: small. |
| `response.audio.delta`: `delta` | Audio / delta. | **Off**; audio engine and adapter required. |
| `response.audio.done` | Audio / done. | **Off**; audio engine and adapter required. |
| `response.audio.transcript.delta`: `delta` | Audio / transcript / delta. | **Off**; audio engine and adapter required. |
| `response.audio.transcript.done` | Audio / transcript / done. | **Off**; audio engine and adapter required. |
| `response.compaction.compacting`: `item_id`, `output_index` | Compaction / compacting. | **Off**; in-stream compaction adapter required. |
| `response.shell_call_command.added`: `command`, `command_index`, `output_index` | Shell call command / added. | **Off**; native shell adapter required. |
| `response.shell_call_command.delta`: `command_index`, `delta`, `output_index`, `obfuscation` | Shell call command / delta. | **Off**; native shell adapter required. |
| `response.shell_call_command.done`: `command`, `command_index`, `output_index` | Shell call command / done. | **Off**; native shell adapter required. |
| `response.shell_call_output_content.delta`: `command_index`, `delta`, `delta.stderr`, `delta.stdout`, `item_id`, `output_index` | Shell call output content / delta. | **Off**; native shell adapter required. |
| `response.shell_call_output_content.done`: `command_index`, `item_id`, `output`, `output.outcome`, `output.stderr`, `output.stdout`, `output.created_by`, `output_index` | Shell call output content / done. | **Off**; native shell adapter required. |

## WebSocket events

The entire WebSocket transport is **Off**. SSE is available separately; Strata does not silently substitute it. Server events shared with SSE have the same shapes plus optional `stream_id`; lifecycle and native-tool limitations remain the same.

| Feature / fields | Official meaning | Strata implementation; remaining work |
| --- | --- | --- |
| `response.create`: `type`, `stream_id`, create fields above | Create request with WS envelope; streaming implicit, stream must be omitted and background is unsupported. | **Off**; transport adapter required. |
| `response.steer`: `input`, `previous_response_id`, `type` | Inject input against previous_response_id into an ongoing request. | **Off**; transport plus live generation/history coordination required. |
| `response.steer.accepted`: `sequence_number`, `steer`, `steer.id`, `steer.previous_response_id`, `type`, `stream_id` | Accepted steering ID and previous response reference. | **Off** with live steering. |
| `response.steer.pending`: `reason`, `required_input`, `sequence_number`, `steer`, `steer.id`, `steer.previous_response_id`, `type`, `stream_id` | Pending steering reason and required input. | **Off** with live steering. |
| `response.steer.failed`: `error`, `error.code`, `error.message`, `error.type`, `sequence_number`, `steer`, `steer.input`, `steer.previous_response_id`, `steer.id`, `type`, `stream_id` | Steering input/reference and invalid-request error. | **Off** with live steering. |
| WebSocket `error`: `error`, `error.code`, `error.message`, `error.param`, `error.type`, `error.headers`, `error.misalignment`, `error.misalignment.detailed_explanation`, `error.misalignment.error_type`, `error.misalignment.steer`, `error.misalignment.steer.message`, `type`, `sequence_number`, `status`, `stream_id` | Error code/message/parameter/type, optional headers/misalignment, status and stream identifier. | **Off**; structured WS transport/error adapter required. |
| Steering message input: `content`, `role`, `type`, `id`, `status` | Message content/role and optional ID/status. | **Off** in steering; ordinary HTTP message input supported. |
| Steering function output: `output`, `type`, `id`, `call_id`, `caller`, `name`, `namespace`, `status` | Function result and matching call/caller/name/namespace/status. | **Off** in steering; ordinary HTTP function output supported. |
| Steering caller: `type`, `caller_id` | Direct call or program caller ID. | **Off** in steering; ordinary HTTP supports direct caller only. |
| Steering pending-input reference: `call_id`, `type`, `execution` | Required custom/search call_id and client/server execution selector. | **Off** in steering. |
| Shared server events | All 59 SSE names above also appear in WS; optional stream_id routes interleaved streams. | **Off**; shared payload schemas are listed once in SSE, not a second independent capability. |

## Beta additions and local extensions

Beta is a separate published contract, not a stable Responses requirement. Shared stable fields retain the statuses above; these additions are not enabled in Strata.

| Feature / fields | Official meaning | Strata implementation; remaining work |
| --- | --- | --- |
| Beta `multi_agent.enabled`, `.max_concurrent_subagents`; SDK `betas` / `responses_multi_agent=v1` header | Enable server-coordinated agents and set concurrency. | **Off**; substantial orchestration service, not an engine flag; client-owned agent tools remain independent. |
| Beta item/event `agent`, `agent.agent_name` | Identify the agent producing an item/event payload. | **Off**; no provider-managed agents; no synthetic agent attribution. |
| Beta `agent_message`: `author`, `content`, `recipient`, `type`, `id`, `agent`, `agent.agent_name` | Author/recipient, agent identity and typed inter-agent content. | **Off**; provider-managed multi-agent service required. |
| Beta `encrypted_content`: `encrypted_content`, `type` | Encrypted inter-agent content. | **Off**; provider-managed multi-agent service required. |
| Beta `Agent text/summary/reasoning content`: `text`, `type` | Typed inter-agent text parts. | **Off**; provider-managed multi-agent service required. |
| Beta `Agent computer_screenshot`: `detail`, `file_id`, `image_url`, `type`, `prompt_cache_breakpoint`, `prompt_cache_breakpoint.mode` | Image URL/file ID, detail and explicit cache breakpoint. | **Off**; provider-managed multi-agent service required. |
| Beta `multi_agent_call`: `action`, `arguments`, `call_id`, `type`, `id`, `agent`, `agent.agent_name` | Agent action and JSON arguments with matching call ID. | **Off**; provider-managed multi-agent service required. |
| Beta `multi_agent_call_output`: `action`, `call_id`, `output`, `output.text`, `output.type`, `output.annotations`, `type`, `id`, `agent`, `agent.agent_name`, `output.logprobs` | Agent action result as annotated/logprob-capable output_text; common annotation shapes above. | **Off**; provider-managed multi-agent service required. |
| Beta `response.inject`: `input`, `response_id`, `type` | Atomically commit client-owned tool results into a waiting active response. | **Off**; WS transport plus multi-agent live orchestration required. |
| Beta `response.inject.created`: `response_id`, `sequence_number`, `type`, `stream_id` | Acknowledge committed input. | **Off**; WS transport plus multi-agent live orchestration required. |
| Beta `response.inject.failed`: `error`, `input`, `response_id`, `sequence_number`, `type`, `stream_id`, `error.code`, `error.message` | Return uncommitted raw input; error response_already_completed/response_not_found. | **Off**; WS transport plus multi-agent live orchestration required. |
| Beta multi-agent actions | `spawn_agent`, `interrupt_agent`, `list_agents`, `send_message`, `followup_task`, `wait_agent`. | **Off**; orchestration service required; not automatically mapped to client tools. |
| Beta SSE/WS shared events | Same stable event families with Beta item/response schemas and agent identity where specified. | **Off** for multi-agent semantics; stable single-agent SSE implementation is independent. |
| Local `client_metadata` | Not a stable Responses parameter; local client-supplied object. | **Partial**; accepted, not rendered/echoed; no generic API semantics. |
| Local `reasoning.context=last_turn` | Compatibility alias outside the official enum. | **Local** alias for current_turn; response reports canonical value. |
| Local named tool-choice `namespace` | Explicit namespace identity beyond the published function/custom choice shape. | **Local**; does not alter the native namespace tool contract. |
| Local input-items `before` | Additional backward cursor beyond the listed endpoint parameters. | **Local**; not portable to all Responses providers. |
| Local output `store`, content `text`/`image_url` aliases | Extra response flag and accepted compatibility content aliases. | **Local** extensions; use stable input_text/input_image when portable behavior is needed. |
| Deprecated fields and tool variants | user, reasoning.generate_summary, local_shell and dated/preview native search/computer variants. | user/summary alias accepted with limits above; native legacy/preview tools **Off**, no silent upgrade. |
