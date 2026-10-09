import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord import app_commands
from commands.mod import setup_mod_commands
from config import conf_permissions_settings as settings
from config.conf_permissions import PermissionsView, PermissionRoleView


class PermissionsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patch = patch.object(settings, 'CONFIG_FILE', Path(self.directory.name) / 'permissions.json')
        self.patch.start()
        self.guild = SimpleNamespace(id=1)
        self.roles = {i: discord.Role(guild=self.guild, state=None, data={
            'id': str(i), 'name': f'role{i}', 'position': position, 'permissions': '0',
        }) for i, position in [(1, 0), (2, 2), (3, 3), (4, 3), (5, 5)]}
        self.guild.get_role = self.roles.get

    def tearDown(self):
        self.patch.stop()
        self.directory.cleanup()

    def allowed(self, role=2, admin=False, guild=None):
        member = SimpleNamespace(top_role=self.roles[role], guild_permissions=discord.Permissions(administrator=admin))
        return settings.can_use_moderator_command(member, guild or self.guild, 'czysc')

    def test_defaults_and_global_threshold(self):
        self.assertFalse(self.allowed())
        self.assertTrue(self.allowed(admin=True))
        settings.set_role(1, 3)
        self.assertFalse(self.allowed(2))
        self.assertTrue(self.allowed(3))
        self.assertTrue(self.allowed(5))
        # Discord resolves roles at equal positions by ID; use its actual ordering.
        self.assertEqual(self.allowed(4), self.roles[4] >= self.roles[3])

    def test_overrides_reset_deleted_role_and_guild_isolation(self):
        settings.set_role(1, 2)
        settings.set_role(1, 5, 'czysc')
        self.assertFalse(self.allowed(3))
        self.assertTrue(self.allowed(5))
        settings.set_role(1, None, 'czysc')
        self.assertFalse(self.allowed(5))
        self.assertTrue(self.allowed(admin=True))
        settings.set_role(1, None, 'czysc', inherit=True)
        self.assertTrue(self.allowed(2))
        self.assertIsNone(settings.load_config(2)['moderator_role_id'])
        settings.set_role(1, 999)
        self.assertFalse(self.allowed(5))
        self.assertTrue(self.allowed(admin=True))

    def test_panel_and_command_denial(self):
        async def run():
            view = PermissionsView(10, self.guild)
            self.assertEqual(len(view.children), 3)
            self.assertTrue(PermissionRoleView(10, self.guild).inherit.disabled)
            self.assertFalse(PermissionRoleView(10, self.guild, 'czysc').inherit.disabled)
            client = discord.Client(intents=discord.Intents.none())
            tree = app_commands.CommandTree(client)
            await setup_mod_commands(client, tree, guild_id=1)
            command = tree.get_command('czysc', guild=discord.Object(id=1))
            interaction = SimpleNamespace(guild=self.guild, user=SimpleNamespace(
                top_role=self.roles[2], guild_permissions=discord.Permissions.none()),
                response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()))
            await command.callback(interaction, 10)
            interaction.response.send_message.assert_awaited_once()
            interaction.response.defer.assert_not_awaited()
            self.assertIsNone(command.default_permissions)
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
