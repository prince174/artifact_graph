"""Bounded, thread-safe response cache for the single graph worker."""
import json
import time
from collections import OrderedDict
from functools import wraps
from threading import RLock


class GraphCache:
    def __init__(self, max_bytes=32 * 1024 * 1024, max_entries=128, ttl=60):
        self.max_bytes, self.max_entries, self.ttl = max_bytes, max_entries, ttl
        self.lock = RLock()
        self.generation = 0
        self.items = OrderedDict()
        self.size = 0

    def invalidate(self):
        with self.lock:
            self.generation += 1
            self.items.clear()
            self.size = 0

    def cached(self, function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            key = repr((args, sorted(kwargs.items())))
            with self.lock:
                generation = self.generation
                entry = self.items.get(key)
                if entry and time.monotonic() - entry[0] < self.ttl:
                    self.items.move_to_end(key)
                    return json.loads(entry[1])
            result = function(*args, **kwargs)
            payload = json.dumps(result).encode()
            with self.lock:
                if generation == self.generation and len(payload) <= self.max_bytes:
                    if old := self.items.pop(key, None):
                        self.size -= len(old[1])
                    self.items[key] = (time.monotonic(), payload)
                    self.size += len(payload)
                    while self.size > self.max_bytes or len(self.items) > self.max_entries:
                        _, old = self.items.popitem(last=False)
                        self.size -= len(old[1])
            return result
        return wrapped


graph_cache = GraphCache()
