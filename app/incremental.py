from collections import OrderedDict


class BoundedCache:
    def __init__(self, max_size: int = 2000):
        self.max_size = max(1, max_size)
        self._items = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key):
        if key not in self._items:
            self.misses += 1
            return None
        self.hits += 1
        self._items.move_to_end(key)
        return self._items[key]

    def put(self, key, value):
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self.max_size:
            self._items.popitem(last=False)

    def clear(self):
        self._items.clear()
        self.hits = self.misses = 0

    def __len__(self):
        return len(self._items)
