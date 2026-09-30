"""Small HTTP adapter. All protocol/inference code lives outside server.py."""
import json
from urllib.parse import parse_qs, urlsplit

from .constraints import loads
from .errors import APIError

MAX_BODY = 8 * 1024 * 1024


def send_stream(handler, events):
    try:
        handler._sse()
        for ev in events:
            if ev is None:
                handler.wfile.write(b": keep-alive\n\n")
            else:
                handler.wfile.write((f"event: {ev['type']}\ndata: " +
                                     json.dumps(ev, ensure_ascii=False) + "\n\n").encode("utf-8"))
            handler.wfile.flush()
    finally:
        events.close()


def route(handler, api):
    url = urlsplit(handler.path)
    path = url.path.rstrip("/")
    if path.startswith("/v1/"):
        path = path[3:]
    if path != "/responses" and not path.startswith("/responses/"):
        return False
    if not handler._authorized():
        return True
    query, method, tail = parse_qs(url.query), handler.command, path[len("/responses"):].strip("/")
    try:
        if method == "POST":
            try:
                length = int(handler.headers.get("Content-Length", 0))
            except ValueError as e:
                raise APIError("invalid Content-Length") from e
            if length < 0 or length > MAX_BODY:
                raise APIError("Responses request body exceeds 8 MiB", code="request_too_large", status=413)
            try:
                req = loads(handler.rfile.read(length).decode("utf-8") or "{}")
            except (ValueError, UnicodeError) as e:
                raise APIError("invalid JSON body", code="invalid_json") from e
            if not tail:
                record, events = api.create(req)
                if req.get("stream"):
                    try:
                        send_stream(handler, events if events is not None else api.replay(record))
                    except OSError:
                        if not record.response["background"]:
                            api.abandon(record)
                elif events is None:
                    handler._json(200, record.snapshot())
                else:
                    for _ in events:
                        pass
                    handler._json(200, record.snapshot())
            elif tail == "input_tokens":
                handler._json(200, api.count(req))
            elif tail == "compact":
                handler._json(200, api.compact(req))
            elif tail.endswith("/cancel") and tail.count("/") == 1:
                handler._json(200, api.cancel(tail.split("/")[0]))
            else:
                raise APIError("Responses route not found", code="not_found", status=404)
        elif method == "GET" and tail:
            if tail.endswith("/input_items") and tail.count("/") == 1:
                handler._json(200, api.input_items(tail.split("/")[0], query))
            elif "/" not in tail:
                record = api.store.get(tail)
                if query.get("stream", ["false"])[0] == "true":
                    try:
                        after = int(query.get("starting_after", [handler.headers.get("Last-Event-ID", -1)])[0])
                    except ValueError as e:
                        raise APIError("starting_after must be an integer", "starting_after") from e
                    if after < -1:
                        raise APIError("starting_after must be nonnegative", "starting_after")
                    try:
                        send_stream(handler, api.replay(record, after))
                    except OSError:
                        pass  # disconnecting a replay subscriber does not cancel its producer
                else:
                    handler._json(200, record.snapshot())
            else:
                raise APIError("Responses route not found", code="not_found", status=404)
        elif method == "DELETE" and tail and "/" not in tail:
            api.store.delete(tail)
            handler.send_response(204)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
        else:
            raise APIError("Responses route not found", code="not_found", status=404)
    except APIError as e:
        handler._json(e.status, e.body())
    except OSError:
        pass  # HTTP peer disconnected
    return True
