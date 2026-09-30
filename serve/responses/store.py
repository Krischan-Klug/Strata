"""Bounded, process-local Responses state. Restarting the server clears it.

Only stored records retain history/events. Active jobs are never silently evicted.
The same condition protects snapshots and wakes background-stream subscribers.
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import json
import threading
import time

from .errors import APIError

TERMINAL = {"completed", "failed", "cancelled", "incomplete"}


def size(value):
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


@dataclass
class Record:
    response: dict
    history: list
    stored: bool
    events: list = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)
    condition: threading.Condition = field(default_factory=threading.Condition)
    touched: float = field(default_factory=time.monotonic)
    bytes: int = 0
    finished: bool = False

    def snapshot(self):
        with self.condition:
            return deepcopy(self.response)

    def publish(self, event):
        with self.condition:
            if self.stored:
                self.events.append(deepcopy(event))
            self.condition.notify_all()


class Store:
    def __init__(self, max_records=128, max_bytes=64 * 1024 * 1024, max_record_bytes=16 * 1024 * 1024,
                 ttl=3600, max_active=4):
        self.records = OrderedDict()
        self.lock = threading.RLock()
        self.max_records, self.max_bytes, self.max_record_bytes = max_records, max_bytes, max_record_bytes
        self.ttl, self.max_active, self.active = ttl, max_active, 0

    def _prune(self, needed=0, slots=0):
        now = time.monotonic()
        for key, record in list(self.records.items()):
            if record.finished and now - record.touched >= self.ttl:
                del self.records[key]
        for key, record in list(self.records.items()):
            if len(self.records) + slots <= self.max_records and sum(r.bytes for r in self.records.values()) + needed <= self.max_bytes:
                break
            if record.finished:
                del self.records[key]

    def allocate(self, record):
        with self.lock:
            if self.active >= self.max_active:
                raise APIError("too many active Responses requests", code="rate_limit_exceeded", status=429)
            if record.stored:
                record.bytes = size(record.history) + size(record.response)
                if record.bytes > self.max_record_bytes:
                    raise APIError("stored request exceeds the Responses retention limit", "input", "request_too_large", 413)
                self._prune(record.bytes, slots=1)
                if len(self.records) >= self.max_records or sum(r.bytes for r in self.records.values()) + record.bytes > self.max_bytes:
                    raise APIError("Responses storage is full of active requests", code="rate_limit_exceeded", status=429)
                self.records[record.response["id"]] = record
            self.active += 1

    def publish(self, record, event):
        with self.lock:
            if record.stored:
                n = size(event)
                self._prune(n)
                if record.bytes + n > self.max_record_bytes or sum(r.bytes for r in self.records.values()) + n > self.max_bytes:
                    raise APIError("Responses event retention limit exceeded", code="storage_limit_exceeded", status=500)
                record.bytes += n
            record.publish(event)

    def release(self, record):
        with self.lock:
            if record.finished:
                return
            self.active -= 1
            record.touched = time.monotonic()
            with record.condition:
                record.finished = True
                record.condition.notify_all()

    def get(self, response_id):
        with self.lock:
            self._prune()
            record = self.records.get(response_id)
            if record is None:
                raise APIError("response not found (unstored, expired or deleted)", "response_id", "not_found", 404)
            record.touched = time.monotonic()
            return record

    def delete(self, response_id):
        with self.lock:
            record = self.get(response_id)
            if record.snapshot()["status"] not in TERMINAL:
                raise APIError("cancel the active background response before deleting it", "response_id", "conflict", 409)
            del self.records[response_id]
        return {"id": response_id, "object": "response.deleted", "deleted": True}

    def close(self):
        with self.lock:
            for record in self.records.values():
                record.cancel.set()
