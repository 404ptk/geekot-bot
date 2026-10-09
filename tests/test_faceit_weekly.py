import asyncio
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from config import conf_faceit_weekly_settings as settings
from config.conf_faceit_weekly import FaceitWeeklyView
from faceit import tygodniowka as weekly


class WeeklyConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patch = patch.object(settings, 'CONFIG_FILE', Path(self.directory.name) / 'weekly.json')
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.directory.cleanup()

    def test_persistence_and_validation(self):
        self.assertEqual(settings.load_config(), settings.DEFAULT_CONFIG)
        settings.update_config(interval_weeks=2)
        settings.update_config(channel_id=123)
        self.assertEqual(settings.load_config(), {'interval_weeks': 2, 'channel_id': 123})
        with self.assertRaises(ValueError):
            settings.update_config(interval_weeks=3)
        self.assertEqual(settings.load_config()['interval_weeks'], 2)
        async def check_panel():
            view = FaceitWeeklyView(1)
            self.assertEqual([option.value for option in view.frequency.options if option.default], ['2'])
            self.assertEqual(view.content().fields[1].value, '<#123>')
        asyncio.run(check_panel())

    def test_schedule_and_summary_period(self):
        async def run_case(interval, last_date, today, expected_send):
            settings.update_config(interval_weeks=interval, channel_id=123)
            state = {'last_run_date': last_date, 'date': last_date, 'stats': {}} if last_date else {}
            channel = SimpleNamespace(guild=None, send=AsyncMock())
            client = SimpleNamespace(get_channel=Mock(return_value=channel))
            fake = SimpleNamespace(get_faceit_player_data=Mock(return_value={'games': {'cs2': {'faceit_elo': 1000}}}))
            with patch.dict(sys.modules, {'faceit_utils': fake}), patch.object(weekly, 'load_weekly_stats', return_value=state), patch.object(weekly, 'save_weekly_stats') as save, patch.object(weekly, 'create_weekly_stats_embed', return_value=object()) as build, patch.object(weekly, 'load_config', return_value={'players': ['NewPlayer']}), patch.object(weekly.discord, 'File', return_value=object()):
                await weekly.run_weekly_summary_if_due(client, today=today)
                if expected_send:
                    channel.send.assert_awaited_once()
                    client.get_channel.assert_called_once_with(123)
                    self.assertEqual(save.call_args.args[0]['stats'], {'NewPlayer': 1000})
                    start = datetime.fromtimestamp(build.call_args.args[0])
                    if last_date:
                        self.assertEqual(start, datetime.strptime(last_date, '%Y-%m-%d'))
                    else:
                        end = datetime.fromtimestamp(build.call_args.args[1])
                        self.assertEqual((end - start).days, interval * 7)
                    # A later poll on the same Monday must not send again.
                    await weekly.run_weekly_summary_if_due(client, today=today)
                    channel.send.assert_awaited_once()
                else:
                    channel.send.assert_not_awaited()
                    save.assert_not_called()
        async def run():
            await run_case(1, '2026-10-05', datetime(2026, 10, 12), True)
            await run_case(2, '2026-10-05', datetime(2026, 10, 12), False)
            await run_case(2, '2026-10-05', datetime(2026, 10, 19), True)
            await run_case(2, None, datetime(2026, 10, 19), True)
            await run_case(1, '2026-10-05', datetime(2026, 10, 13), False)
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
