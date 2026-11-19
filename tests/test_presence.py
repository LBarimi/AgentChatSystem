from http.client import HTTPConnection
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Condition, Event, Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from relay.presence import ConnectedUsers, computer_name
from relay.server import Handler, LocalHTTPServer, Room
from relay.settings import Settings


class UserPresence(unittest.TestCase):
    def setUp(self):
        self.changed = Condition()
        self.notifications = 0
        self.users = ConnectedUsers(self.changed, self.notify)

    def notify(self):
        self.notifications += 1
        self.changed.notify_all()

    def wait_for(self, predicate):
        deadline = monotonic() + 3
        while monotonic() < deadline:
            if predicate():
                return
            sleep(0.01)
        self.assertTrue(predicate())

    def test_local_name_remote_lookup_and_ip_fallback(self):
        with patch('relay.presence.socket.gethostname', return_value='SERVER-PC') as local, \
                patch('relay.presence.socket.gethostbyaddr', return_value=('OFFICE-PC.local.', [], [])) as remote:
            self.assertEqual(computer_name('127.0.0.1'), 'SERVER-PC')
            remote.assert_not_called()
            self.assertEqual(computer_name('192.168.1.20'), 'OFFICE-PC.local')
            local.assert_called_once_with()
            remote.assert_called_once_with('192.168.1.20')
        for result in (None, '192.168.1.20', '', 'bad\nname'):
            with self.subTest(result=result), patch('relay.presence.socket.gethostbyaddr') as remote:
                if result is None:
                    remote.side_effect = socket.herror('No reverse record')
                else:
                    remote.return_value = (result, [], [])
                self.assertIsNone(computer_name('192.168.1.20'))

    def test_same_ip_tabs_share_user_until_last_disconnect(self):
        with patch('relay.presence.computer_name', return_value='DESKTOP-한국어') as resolve:
            self.users.connect('192.168.1.20')
            self.wait_for(lambda: self.users.workers == 0)
            resolved_notifications = self.notifications
            self.users.connect('192.168.1.20')
            self.assertEqual(self.users.snapshot(), [{'ip': '192.168.1.20', 'name': 'DESKTOP-한국어'}])
            self.users.disconnect('192.168.1.20')
            self.assertEqual(len(self.users.snapshot()), 1)
            self.assertEqual(self.notifications, resolved_notifications)
            self.users.disconnect('192.168.1.20')
            self.assertEqual(self.users.snapshot(), [])
            self.assertEqual(self.notifications, resolved_notifications + 1)
            self.users.connect('192.168.1.20')
            self.assertEqual(self.users.snapshot()[0]['name'], 'DESKTOP-한국어')
            resolve.assert_called_once_with('192.168.1.20')

    def test_failed_lookup_is_cached_and_expires_on_reconnect(self):
        with patch('relay.presence.computer_name', return_value=None) as resolve:
            self.users.connect('10.0.0.2')
            self.wait_for(lambda: self.users.workers == 0)
            self.assertEqual(self.users.snapshot(), [{'ip': '10.0.0.2', 'name': '10.0.0.2'}])
            self.users.disconnect('10.0.0.2')
            self.users.connect('10.0.0.2')
            self.assertEqual(resolve.call_count, 1)
            self.users.disconnect('10.0.0.2')
            self.users.cache['10.0.0.2'] = (0, None)
            resolve.return_value = 'LAPTOP'
            self.users.connect('10.0.0.2')
            self.wait_for(lambda: self.users.workers == 0)
            self.assertEqual(self.users.snapshot()[0]['name'], 'LAPTOP')
            self.assertEqual(resolve.call_count, 2)

    def test_slow_lookup_keeps_presence_responsive_and_workers_bounded(self):
        release = Event()
        self.addCleanup(release.set)

        def slow_name(address):
            release.wait(5)
            return 'PC-' + address.rsplit('.', 1)[1]

        with patch('relay.presence.computer_name', side_effect=slow_name):
            for number in range(1, 13):
                self.users.connect(f'10.0.0.{number}')
            self.assertEqual(len(self.users.snapshot()), 12)
            self.assertEqual(self.users.snapshot()[0]['name'], '10.0.0.1')
            self.assertLessEqual(self.users.workers, 2)
            self.users.disconnect('10.0.0.1')
            self.assertEqual(len(self.users.snapshot()), 11)
            release.set()
            self.wait_for(lambda: self.users.workers == 0)
            self.assertFalse(self.users.pending)
            self.assertTrue(all(user['name'].startswith('PC-') for user in self.users.snapshot()))
            self.assertNotIn('10.0.0.1', [user['ip'] for user in self.users.snapshot()])

    def test_name_cache_does_not_grow_without_limit(self):
        self.users.CACHE_LIMIT = 2
        with patch('relay.presence.computer_name', return_value=None):
            for number in range(1, 5):
                address = f'10.0.0.{number}'
                self.users.connect(address)
                self.wait_for(lambda: self.users.workers == 0)
                self.users.disconnect(address)
            self.assertEqual(list(self.users.cache), ['10.0.0.3', '10.0.0.4'])


class BrowserPresence(unittest.TestCase):
    def test_real_event_streams_track_peer_ip_and_release_on_close(self):
        with TemporaryDirectory() as temporary, patch('relay.presence.computer_name', return_value='TEST-PC'):
            root = Path(temporary)
            server = LocalHTTPServer(('127.0.0.1', 0), Handler)
            server.room = Room(root / 'room.sqlite3')
            server.settings = Settings(root / 'settings.ini')
            thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
            thread.start()
            clients = []

            def pulse_until(predicate):
                deadline = monotonic() + 3
                while monotonic() < deadline:
                    with server.room.changed:
                        if predicate():
                            return
                        server.room._notify()
                    sleep(0.02)
                self.assertTrue(predicate())

            def close_stream(pair):
                connection, response, transport = pair
                transport.shutdown(socket.SHUT_RDWR)
                response.close()
                connection.close()

            try:
                # Ordinary HTTP/CLI reads do not create human users.
                self.assertEqual(server.room.snapshot('general')['users'], [])
                for _ in range(2):
                    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                    connection.request('GET', '/api/events', headers={'X-Forwarded-For': '10.0.0.99'})
                    transport = connection.sock
                    response = connection.getresponse()
                    clients.append((connection, response, transport))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.readline(), b'event: change\n')
                    self.assertTrue(response.readline().startswith(b'data: '))
                    self.assertEqual(response.readline(), b'\n')
                pulse_until(lambda: server.room.users.snapshot() == [{'ip': '127.0.0.1', 'name': 'TEST-PC'}])
                channel = server.room.create_channel({'name': 'Other'})
                self.assertEqual(server.room.snapshot(channel['id'])['users'], [])
                close_stream(clients.pop())
                pulse_until(lambda: server.room.streams == 1)
                self.assertEqual(len(server.room.snapshot('general')['users']), 1)
                close_stream(clients.pop())
                pulse_until(lambda: server.room.streams == 0)
                self.assertEqual(server.room.snapshot('general')['users'], [])
            finally:
                for pair in clients:
                    close_stream(pair)
                pulse_until(lambda: server.room.streams == 0)
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()
                server.room.db.close()


if __name__ == '__main__':
    unittest.main()
