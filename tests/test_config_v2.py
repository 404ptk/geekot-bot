import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord import app_commands
from config.conf_menu import ConfigHomeView, setup_config_commands
from config.conf_faceit import FaceitView, FaceitLiveView, RemovePlayerView, ChannelView
from config.conf_relations import RelationsView, RemoveRelationUserView
from config.conf_permissions import PermissionsView, PermissionRoleView
from config.conf_steam import SteamView, SteamChannelView
from config.conf_faceit_weekly import FaceitWeeklyView, WeeklyChannelView
from config import conf_steam_settings as steam_settings


class ConfigV2Tests(unittest.IsolatedAsyncioTestCase):
    def interaction(self, *, administrator=True, user_id=1):
        return SimpleNamespace(guild=SimpleNamespace(id=1, get_role=lambda role_id: None),
            permissions=discord.Permissions(administrator=administrator),
            user=SimpleNamespace(id=user_id), message=SimpleNamespace(edit=AsyncMock()),
            response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
            original_response=AsyncMock())

    async def test_every_screen_serializes_as_v2(self):
        guild = self.interaction().guild
        views = [ConfigHomeView(1), FaceitView(1), FaceitLiveView(1), RemovePlayerView(1),
                 ChannelView(1), RelationsView(1), RemoveRelationUserView(1),
                 PermissionsView(1, guild), PermissionRoleView(1, guild),
                 PermissionRoleView(1, guild, 'czysc'), SteamView(1), SteamChannelView(1),
                 FaceitWeeklyView(1), WeeklyChannelView(1)]
        for view in views:
            with self.subTest(screen=type(view).__name__):
                view.render(view.content())
                self.assertIsInstance(view, discord.ui.LayoutView)
                self.assertEqual(view.to_components()[0]['type'], 17)
                self.assertLessEqual(view.total_children_count, 40)
                self.assertTrue(view.has_components_v2())
                for item in view.walk_children():
                    if isinstance(item, (discord.ui.Button, discord.ui.Select,
                                         discord.ui.ChannelSelect, discord.ui.RoleSelect)):
                        self.assertIs(item.view, view)
                        self.assertIsNotNone(item.parent)

    async def test_command_opens_private_v2_panel(self):
        client = discord.Client(intents=discord.Intents.none())
        tree = app_commands.CommandTree(client)
        await setup_config_commands(client, tree, guild_id=1)
        interaction = self.interaction()
        await tree.get_command('config', guild=discord.Object(id=1)).callback(interaction)
        kwargs = interaction.response.send_message.call_args.kwargs
        self.assertTrue(kwargs['ephemeral'])
        self.assertNotIn('embed', kwargs)
        self.assertNotIn('content', kwargs)
        self.assertTrue(kwargs['view'].has_components_v2())

    async def test_nested_actions_navigation_and_expiration(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(steam_settings, 'CONFIG_FILE', Path(directory) / 'steam.json'):
                view = SteamView(1).render(SteamView(1).content())
                interaction = self.interaction()
                await view.official.callback(interaction)
                self.assertFalse(steam_settings.load_config()['official_enabled'])
                kwargs = interaction.response.edit_message.call_args.kwargs
                self.assertNotIn('embed', kwargs)
                next_view = kwargs['view']
                self.assertEqual(next_view.official.label, 'Włącz Steam')
                await next_view.channel.callback(interaction)
                channel_view = interaction.response.edit_message.call_args.kwargs['view']
                self.assertIsInstance(channel_view, SteamChannelView)
                await channel_view.back.callback(interaction)
                returned = interaction.response.edit_message.call_args.kwargs['view']
                self.assertIsInstance(returned, SteamView)
                await returned.on_timeout()
                self.assertTrue(all(item.disabled for item in returned.walk_children()
                                    if hasattr(item, 'disabled')))
                self.assertNotIn('content', interaction.message.edit.call_args.kwargs)
                self.assertIn('Panel wygasł', str(returned.to_components()))

    async def test_navigation_to_guild_scoped_permissions(self):
        view = ConfigHomeView(1)
        select = view.children[0]
        view.render(view.content())
        select._values = ['permissions']
        interaction = self.interaction()
        await select.callback(interaction)
        next_view = interaction.response.edit_message.call_args.kwargs['view']
        self.assertIsInstance(next_view, PermissionsView)
        self.assertTrue(next_view.has_components_v2())

    async def test_admin_and_owner_checks(self):
        view = ConfigHomeView(1).render(ConfigHomeView(1).content())
        self.assertFalse(await view.interaction_check(self.interaction(administrator=False)))
        self.assertFalse(await view.interaction_check(self.interaction(user_id=2)))
        self.assertTrue(await view.interaction_check(self.interaction()))
