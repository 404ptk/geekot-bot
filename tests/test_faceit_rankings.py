import unittest
from unittest.mock import Mock, patch

import requests

from faceit.rankings import get_player_ranking, player_ranking_line


class RankingRequestTests(unittest.TestCase):
    def test_country_filter_and_regional_request(self):
        with patch('faceit.rankings.requests.get', return_value=Mock(status_code=200, json=Mock(return_value={'position': 5213}))) as get:
            self.assertEqual(get_player_ranking('player', 'EU', 'test-key', 'PL'), 5213)
            self.assertEqual(get.call_args.kwargs['params'], {'limit': 1, 'country': 'pl'})
            self.assertEqual(get.call_args.kwargs['timeout'], 10)
            self.assertEqual(get_player_ranking('player', 'EU', 'test-key'), 5213)
            self.assertEqual(get.call_args.kwargs['params'], {'limit': 1})

    def test_missing_invalid_and_failed_ranking(self):
        for value in (None, 0, -1, True, '123'):
            with self.subTest(position=value), patch('faceit.rankings.requests.get', return_value=Mock(status_code=200, json=Mock(return_value={'position': value}))):
                self.assertIsNone(get_player_ranking('player', 'EU', 'test-key'))
        with patch('faceit.rankings.requests.get', side_effect=requests.Timeout):
            self.assertIsNone(get_player_ranking('player', 'EU', 'test-key'))
        with patch('faceit.rankings.requests.get', return_value=Mock(status_code=404)):
            self.assertIsNone(get_player_ranking('player', 'EU', 'test-key'))


class RankingLineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Exercise ranking logic without depending on the test runner's thread pool.
        async def inline_thread(function, *args):
            return function(*args)
        self.thread_patch = patch('faceit.rankings.asyncio.to_thread', inline_thread)
        self.thread_patch.start()
        self.addCleanup(self.thread_patch.stop)

    def player(self, country='pl'):
        return {'player_id': 'player', 'country': country, 'games': {'cs2': {'region': 'EU'}}}

    async def test_country_and_region_format(self):
        with patch('faceit.rankings.get_player_ranking', side_effect=lambda pid, region, key, country=None: 5213 if country else 177927):
            self.assertEqual(await player_ranking_line(self.player(), 'key'), '🇵🇱 PL #5213 · 🌍 EU #177927')

    async def test_partial_results(self):
        with patch('faceit.rankings.get_player_ranking', side_effect=lambda pid, region, key, country=None: None if country else 177927):
            self.assertEqual(await player_ranking_line(self.player(), 'key'), '🌍 EU #177927')
        with patch('faceit.rankings.get_player_ranking', return_value=None):
            self.assertEqual(await player_ranking_line(self.player(), 'key'), '')

    async def test_missing_country_or_region(self):
        with patch('faceit.rankings.get_player_ranking', return_value=123) as get:
            self.assertEqual(await player_ranking_line(self.player(''), 'key'), '🌍 EU #123')
            get.assert_called_once_with('player', 'EU', 'key')
        with patch('faceit.rankings.get_player_ranking') as get:
            self.assertEqual(await player_ranking_line({'player_id': 'player'}, 'key'), '')
            self.assertEqual(await player_ranking_line(self.player(), None), '')
            get.assert_not_called()
