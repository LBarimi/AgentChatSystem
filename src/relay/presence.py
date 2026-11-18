"""Browser presence and best-effort computer names, grouped by peer IP."""

from collections import OrderedDict, deque
from ipaddress import ip_address
import socket
from threading import Thread
from time import monotonic


def computer_name(address):
    try:
        name = socket.gethostname() if ip_address(address).is_loopback else socket.gethostbyaddr(address)[0]
    except (OSError, UnicodeError):
        return None
    name = name.strip().rstrip('.')
    if not name or len(name) > 253 or not name.isprintable():
        return None
    try:
        ip_address(name)
    except ValueError:
        return name
    return None


class ConnectedUsers:
    CACHE_SECONDS = 300
    CACHE_LIMIT = 128
    WORKERS = 2
    QUEUE_LIMIT = 12

    def __init__(self, changed, notify):
        self.changed = changed
        self.notify = notify
        self.active = {}
        self.cache = OrderedDict()
        self.pending = set()
        self.queue = deque()
        self.workers = 0

    def connect(self, address):
        with self.changed:
            if address in self.active:
                self.active[address]['connections'] += 1
                return
            cached = self.cache.get(address)
            fresh = cached is not None and cached[0] > monotonic()
            self.active[address] = {'connections': 1, 'name': (cached[1] if fresh else None) or address}
            self.notify()
            if not fresh and address not in self.pending and len(self.queue) < self.QUEUE_LIMIT:
                self.pending.add(address)
                self.queue.append(address)
                if self.workers < self.WORKERS:
                    # OS name resolution can be slow. Bound daemon workers so a
                    # stalled lookup cannot block chat, connection setup or exit.
                    self.workers += 1
                    Thread(target=self._resolve, name='ACS-computer-name', daemon=True).start()

    def disconnect(self, address):
        with self.changed:
            user = self.active.get(address)
            if user is None:
                return
            user['connections'] -= 1
            if not user['connections']:
                del self.active[address]
                self.notify()

    def snapshot(self):
        with self.changed:
            return [{'ip': address, 'name': user['name']} for address, user in self.active.items()]

    def _resolve(self):
        while True:
            with self.changed:
                if not self.queue:
                    self.workers -= 1
                    return
                address = self.queue.popleft()
                if address not in self.active:
                    self.pending.remove(address)
                    continue
            name = computer_name(address)
            with self.changed:
                self.pending.remove(address)
                self.cache[address] = (monotonic() + self.CACHE_SECONDS, name)
                self.cache.move_to_end(address)
                while len(self.cache) > self.CACHE_LIMIT:
                    self.cache.popitem(last=False)
                user = self.active.get(address)
                if user is not None and user['name'] != (name or address):
                    user['name'] = name or address
                    self.notify()
