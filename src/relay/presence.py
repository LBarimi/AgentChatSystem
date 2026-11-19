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
        self.sessions = {}

    def open_session(self, address, channel_id, client_id):
        with self.changed:
            key = (address, client_id)
            previous = self.sessions.get(key)
            if previous:
                self.disconnect(address, previous[1])
            token = object()
            self.sessions[key] = (token, channel_id)
            self.connect(address, channel_id)
            return key, token

    def close_session(self, lease):
        key, token = lease
        with self.changed:
            current = self.sessions.get(key)
            if current and current[0] is token:
                del self.sessions[key]
                self.disconnect(key[0], current[1])

    def connect(self, address, channel_id='general'):
        with self.changed:
            if channel_id is None:
                return
            if address in self.active:
                user = self.active[address]
                user['connections'] += 1
                user['channels'][channel_id] = user['channels'].get(channel_id, 0) + 1
                if user['channels'][channel_id] == 1:
                    self.notify()
                return
            cached = self.cache.get(address)
            fresh = cached is not None and cached[0] > monotonic()
            self.active[address] = {'connections': 1, 'channels': {channel_id: 1}, 'name': (cached[1] if fresh else None) or address}
            self.notify()
            if not fresh and address not in self.pending and len(self.queue) < self.QUEUE_LIMIT:
                self.pending.add(address)
                self.queue.append(address)
                if self.workers < self.WORKERS:
                    # OS name resolution can be slow. Bound daemon workers so a
                    # stalled lookup cannot block chat, connection setup or exit.
                    self.workers += 1
                    Thread(target=self._resolve, name='ACS-computer-name', daemon=True).start()

    def disconnect(self, address, channel_id='general'):
        with self.changed:
            user = self.active.get(address)
            if user is None or channel_id not in user['channels']:
                return
            user['connections'] -= 1
            user['channels'][channel_id] -= 1
            if not user['channels'][channel_id]:
                del user['channels'][channel_id]
                changed = True
            else:
                changed = False
            if not user['connections']:
                del self.active[address]
            if changed:
                self.notify()

    def snapshot(self, channel_id='general'):
        with self.changed:
            return [{'ip': address, 'name': user['name']} for address, user in self.active.items()
                    if channel_id in user['channels']]

    def identity(self, address):
        """Use the actual HTTP peer; never wait for a network name lookup."""
        if address is None:
            return None
        with self.changed:
            active = self.active.get(address)
            cached = self.cache.get(address)
            name = active['name'] if active else cached[1] if cached and cached[0] > monotonic() else None
            if (not name or name == address) and ip_address(address).is_loopback:
                name = computer_name(address)
            return {'ip': address, 'name': name or address}

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
