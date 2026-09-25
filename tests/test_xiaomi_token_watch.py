import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location(
    'watch', Path(__file__).resolve().parents[1] / 'scripts/xiaomi_token_watch.py')
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def streams(count=None, receiver=2, audio=False):
    p = {'id': 1, 'url': 'xiaomi://test', 'receivers': []}
    if count is not None:
        p['receivers'] = [{'id': receiver, 'bytes': count,
                          'codec': {'codec_type': 'audio' if audio else 'video'}}]
    return {'cam': {'producers': [p]}}


ERROR = '12:00:00 WRN [rtsp] error="streams: 401 Unauthorized" stream=cam\n'
START = '12:01:00 INF go2rtc platform=darwin/arm64 version=1.9.14\n'


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = 2000000000
        self.m = watch.Monitor(self.temp.name, sleep=lambda _: None, now=lambda: self.clock)
        self.m.log = Mock()
        self.m.alert = Mock()
        self.m.restart = Mock(return_value=True)
        self.m.streams = Mock(return_value=streams())

    def append(self, text):
        with self.m.log_path.open('a') as f:
            f.write(text)
        # Initial import intentionally skips old log files.
        import os
        os.utime(self.m.log_path, (self.clock, self.clock))

    def test_401_restarts_and_verifies_video_without_credentials(self):
        self.append(ERROR)
        self.m.streams.side_effect = [streams(), streams(), streams(100), streams(200)]
        self.m.run()
        self.m.restart.assert_called_once()
        self.assertEqual(self.m.state['pending'], [])
        self.assertEqual(self.m.state['last_restart'], self.clock)
        self.m.alert.assert_not_called()

    def test_historical_401_before_restart_is_ignored(self):
        self.append(ERROR + START)
        self.m.run()
        self.m.restart.assert_not_called()

    def test_recovered_video_prevents_unnecessary_restart(self):
        self.append(ERROR)
        self.m.streams.side_effect = [streams(100), streams(200)]
        self.m.run()
        self.m.restart.assert_not_called()

    def test_no_repeat_for_same_log_even_after_cooldown(self):
        self.append(ERROR)
        self.m.run()
        self.clock += 7200
        self.m.run()
        self.assertEqual(self.m.restart.call_count, 1)

    def test_new_401_respects_persistent_cooldown(self):
        self.append(ERROR)
        self.m.run()
        saved = json.loads(self.m.state_path.read_text())
        self.assertEqual(saved['last_restart'], self.clock)
        again = watch.Monitor(self.temp.name, sleep=lambda _: None, now=lambda: self.clock + 600)
        again.streams = Mock(return_value=streams())
        again.alert = Mock()
        again.restart = Mock()
        self.append(ERROR)
        again.run()
        again.restart.assert_not_called()
        again.alert.assert_called_once()
        self.clock += 3601
        self.append(ERROR)
        self.m.state = again.state
        self.m.run()
        self.assertEqual(self.m.restart.call_count, 2)

    def test_failed_restart_is_rate_limited(self):
        self.append(ERROR)
        self.m.restart.return_value = False
        self.m.run()
        self.assertEqual(self.m.alert.call_args.args[0], 'xiaomi_restart_failed')
        self.clock += 600
        self.append(ERROR)
        self.m.run()
        self.assertEqual(self.m.restart.call_count, 1)

    def test_web_401_non_xiaomi_and_timeouts_do_not_restart(self):
        self.append('WRN [api] 401 Unauthorized\n'
                    'WRN [rtsp] error="streams: 401 Unauthorized" stream=other\n'
                    'WRN [rtsp] error="streams: read udp: i/o timeout" stream=cam\n')
        self.m.run()
        self.m.restart.assert_not_called()

    def test_api_unreachable_does_not_consume_error(self):
        self.append(ERROR)
        self.m.streams.side_effect = OSError()
        self.m.run()
        self.assertNotIn('cursor', self.m.state)
        self.m.restart.assert_not_called()
        self.assertEqual(self.m.alert.call_args.args[0], 'go2rtc_down')

    def test_pending_recovery_is_verified_on_later_run(self):
        self.append(ERROR)
        self.m.run()
        self.assertEqual(self.m.state['pending'], ['cam'])
        self.m.streams.side_effect = [streams(500), streams(600)]
        self.m.run()
        self.assertEqual(self.m.state['pending'], [])
        self.assertEqual(self.m.restart.call_count, 1)

    def test_rotation_truncation_and_partial_lines(self):
        self.append(START + ERROR)
        self.m.read_new_log()
        self.m.log_path.write_text(ERROR[:-1])
        self.assertEqual(self.m.read_new_log(), '')
        self.append('\n')
        self.assertIn('401', self.m.read_new_log())
        self.m.log_path.rename(self.m.log_path.with_suffix('.old'))
        self.append(ERROR)
        self.assertIn('401', self.m.read_new_log())

    def test_audio_growth_or_new_receiver_is_not_video_progress(self):
        self.assertFalse(watch.growing(streams(1, audio=True), streams(100, audio=True), {'cam'}))
        self.assertFalse(watch.growing(streams(1), streams(100, receiver=3), {'cam'}))

    def test_live_source_format_and_disconnected_source_detection(self):
        active = {'cam': {'producers': [{'format_name': 'xiaomi/miss'}]}}
        self.assertEqual(watch.xiaomi_stream_names(active), {'cam'})
        self.assertEqual(watch.xiaomi_stream_names(streams()), {'cam'})
        empty = {'cam': {'producers': []}}
        self.assertEqual(watch.xiaomi_stream_names(empty, ['cam']), {'cam'})
        changed = {'cam': {'producers': [{'url': 'rtsp://test'}]}}
        self.assertEqual(watch.xiaomi_stream_names(changed, ['cam']), set())

    def test_real_launchctl_command(self):
        from unittest.mock import patch
        import os
        self.m.restart = watch.Monitor.restart.__get__(self.m)
        with patch.object(watch.subprocess, 'run') as run:
            run.return_value.returncode = 0
            self.assertTrue(self.m.restart())
            self.assertEqual(run.call_args.args[0],
                             ['/bin/launchctl', 'kickstart', '-k', f'gui/{os.getuid()}/com.go2rtc'])

    def test_concurrent_invocation_exits_without_monitoring(self):
        from unittest.mock import patch
        with patch.object(watch, 'ROOT', Path(self.temp.name)):
            with (self.m.logs / 'xiaomi-token-watch.lock').open('a') as lock:
                watch.fcntl.flock(lock, watch.fcntl.LOCK_EX | watch.fcntl.LOCK_NB)
                with patch.object(watch, 'Monitor') as monitor:
                    watch.main()
                    monitor.assert_not_called()

    def test_real_alert_deduplicates_and_clears(self):
        import os
        from unittest.mock import patch
        self.m.alert = watch.Monitor.alert.__get__(self.m)
        with patch.dict(os.environ, {'HA_IP': '', 'HA_TOKEN': ''}):
            self.m.alert('xiaomi_401', 'test')
            self.m.alert('xiaomi_401', 'test')
        self.assertEqual(self.m.log.call_count, 2)  # message and skipped notification
        self.m.clear_alert()
        self.assertFalse(self.m.alert_path.exists())


if __name__ == '__main__':
    unittest.main()
