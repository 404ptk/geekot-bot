import discord
from discord import app_commands
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

HIDDEN_HELP_COMMANDS = {"wspomnienie", "wspomnienia", "ytshorts"}

HELP_CATEGORIES = (
    ("faceit", "🎮 Faceit", "Profile, mapy, mecze, analiza i porównania", {"faceit", "maps", "analiza", "last", "compare", "discordfaceit", "scout", "sieroty"}),
    ("cs2", "🎯 CS2", "Instanty, wyzwania i ceny skrzynek", {"instant", "wyzwania", "skrzynki"}),
    ("masny", "📊 Masny", "Tabela, wymówki i spawn", {"masny", "resetmasny", "wymowki", "spawn"}),
    ("games", "🕹️ Gry i wyjazdy", "Lista gier, Minecraft i wakacje", {"gry", "ile", "ilereset", "wakacje"}),
    ("community", "👥 Serwer i relacje", "Aktywność, ranking, avatary i relacje", {"aktywnosc", "ranking", "avatar", "relacje", "zgoda", "dodajrelacje"}),
    ("admin", "⚙️ Moderacja i konfiguracja", "Kanały, ustawienia, oferty i komendy slash", {"config", "zamknij", "otworz", "czysc", "sync", "guildsync", "clearcmds", "clearglobalcmds", "slashlist", "gslashlist", "ofertyfiltry"}),
    ("other", "📜 Pomoc i pozostałe", "Pomoc, changelog i inne komendy", set()),
)


def help_pages(commands):
    """Build bounded pages from the registered commands, including subcommands."""
    blocks = []

    def append_command(command):
        if isinstance(command, app_commands.Group):
            for child in command.commands:
                append_command(child)
            return
        if not isinstance(command, app_commands.Command):
            return
        parameters = " ".join(
            f"<{parameter.display_name}>" if parameter.required else f"[{parameter.display_name}]"
            for parameter in command.parameters
        )
        signature = f"/{command.qualified_name} {parameters}".rstrip()
        blocks.append(f"**`{signature}`**\n{command.description}")

    for command in commands:
        append_command(command)
    pages = []
    current = ""
    for block in blocks:
        if current and len(current) + len(block) + 2 > 3500:
            pages.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        pages.append(current)
    return pages or ["Brak zarejestrowanych komend w tej kategorii."]


class HelpSelect(discord.ui.Select):
    def __init__(self, selected):
        options = [discord.SelectOption(label="📜 Wszystkie kategorie", value="home", default=selected == "home")]
        options.extend(discord.SelectOption(label=title, value=key, description=description, default=selected == key)
                       for key, title, description, _ in HELP_CATEGORIES)
        super().__init__(placeholder="Wybierz kategorię pomocy…", options=options)

    async def callback(self, interaction):
        # Rendering removes this select from its view, so retain the owner first.
        view = self.view
        view.category = self.values[0]
        view.page = 0
        view.render()
        await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())


class HelpPageButton(discord.ui.Button):
    def __init__(self, direction, disabled):
        super().__init__(label="Poprzednia" if direction < 0 else "Następna",
                         style=discord.ButtonStyle.secondary, disabled=disabled)
        self.direction = direction

    async def callback(self, interaction):
        view = self.view
        view.page += self.direction
        view.render()
        await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())


class HelpView(discord.ui.LayoutView):
    def __init__(self, owner_id, commands):
        super().__init__(timeout=600)
        commands = [command for command in commands if command.name not in HIDDEN_HELP_COMMANDS]
        self.owner_id = owner_id
        self.message = None
        self.category = "home"
        self.page = 0
        assigned = set().union(*(entry[3] for entry in HELP_CATEGORIES))
        self.pages = {
            key: help_pages([command for command in commands
                             if command.name in names or (key == "other" and command.name not in assigned)])
            for key, _, _, names in HELP_CATEGORIES
        }
        self.render()

    def render(self):
        self.clear_items()
        container = discord.ui.Container(accent_color=0x5865F2)
        if self.category == "home":
            container.add_item(discord.ui.TextDisplay("## 📜 Pomoc Geekota\nWybierz kategorię, aby zobaczyć komendy i sposób ich użycia."))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay("\n\n".join(
                f"**{title}**\n{description}" for _, title, description, _ in HELP_CATEGORIES)))
        else:
            _, title, description, _ = next(entry for entry in HELP_CATEGORIES if entry[0] == self.category)
            pages = self.pages[self.category]
            self.page = max(0, min(self.page, len(pages) - 1))
            container.add_item(discord.ui.TextDisplay(f"## {title}\n{description}"))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(pages[self.page]))
            if len(pages) > 1:
                container.add_item(discord.ui.TextDisplay(f"-# Strona {self.page + 1} z {len(pages)}"))
                container.add_item(discord.ui.ActionRow(
                    HelpPageButton(-1, self.page == 0), HelpPageButton(1, self.page == len(pages) - 1)))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(HelpSelect(self.category)))
        self.footer = discord.ui.TextDisplay(
            "-# <parametr> — wymagany • [parametr] — opcjonalny\n"
            "-# Dostęp do komend zależy od Twoich uprawnień. Panel prywatny • wygasa po 10 minutach bezczynności")
        container.add_item(self.footer)
        self.add_item(container)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Otwórz własny panel przez /pomoc.", ephemeral=True)
        return False

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        self.footer.content = "-# Panel wygasł. Wpisz /pomoc, aby otworzyć go ponownie."
        if self.message:
            try:
                await self.message.edit(view=self, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        logger.error("Help panel error", exc_info=(type(error), error, error.__traceback__))
        message = "Nie udało się wyświetlić pomocy. Otwórz panel ponownie przez /pomoc."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            logger.warning("Could not deliver help panel error response", exc_info=True)


async def setup_help_commands(client: discord.Client, tree: app_commands.CommandTree, guild_id: int = None):
    guild = discord.Object(id=guild_id) if guild_id else None

    async def send_help(interaction):
        # Guild commands override global commands with the same name.
        commands = {command.name: command for command in tree.get_commands()}
        if interaction.guild_id:
            commands.update({command.name: command for command in tree.get_commands(guild=discord.Object(id=interaction.guild_id))})
        view = HelpView(interaction.user.id, list(commands.values()))
        await interaction.response.send_message(view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        view.message = await interaction.original_response()

    # --- Changelog: łatwy do edycji format (grupowanie zmian per data) ---
    project_root = Path(__file__).resolve().parent.parent
    CHANGELOG_FILE = project_root / "txt" / "changelog.json"

    # Domyślny changelog (gdy nie ma pliku txt/changelog.json)
    DEFAULT_CHANGELOG = {
        "2025-09-03": [
            {"title": "Dodano komendę /changelog", "desc": "Wyświetla listę najnowszych zmian w bocie (data -> tytuł/opis)."},
        ]
    }

    def load_changelog():
        try:
            if CHANGELOG_FILE.exists():
                with open(CHANGELOG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Oczekiwany format: { "YYYY-MM-DD": [ {"title": str, "desc": str}, ... ], ... }
                    if isinstance(data, dict):
                        return data
        except Exception as e:
            print(f"[Changelog] Error reading {CHANGELOG_FILE}: {e}")
        return DEFAULT_CHANGELOG

    def create_changelog_embed():
        user = client.get_user(1293142451007131740)
        avatar_url = user.display_avatar.url if user else None

        embed = discord.Embed(
            title="📝 Changelog",
            description="",
            color=discord.Color.orange()
        )

        changelog_map = load_changelog()
        if not changelog_map:
            embed.add_field(name="Brak wpisów", value="Changelog jest pusty.", inline=False)
        else:
            # Pokaż do 7 ostatnich dni zmian; zachowaj kolejność z pliku (gdy ktoś chce manualnie sterować)
            # Jeżeli chcesz wymusić sortowanie po dacie malejąco, odkomentuj linie sortujące poniżej.
            # sorted_items = sorted(changelog_map.items(), key=lambda kv: kv[0], reverse=True)
            items_iterable = list(changelog_map.items())[:7]

            for date_str, items in items_iterable:
                if not isinstance(items, list):
                    continue
                lines = []
                current_len = 0
                for it in items:
                    title = str(it.get("title", "Bez tytułu")).strip()
                    desc = str(it.get("desc", "")).strip()
                    line = f"• {title}"
                    if desc:
                        line += f" — {desc}"
                    # Ogranicz pole embeda do ~1000 znaków na bezpieczeństwo
                    if current_len + len(line) + 1 > 1000:
                        lines.append("• …")
                        break
                    lines.append(line)
                    current_len += len(line) + 1
                value = "\n".join(lines) if lines else "—"
                embed.add_field(name=date_str, value=value, inline=False)

        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        embed.set_footer(text="Geekot - Changelog")
        return embed

    # Original pomoc command
    @tree.command(
        name="pomoc",
        description="Otwiera panel pomocy z wyborem kategorii",
        guild=guild
    )
    async def pomoc(interaction: discord.Interaction):
        await send_help(interaction)
        
    # Add geek as an alias for pomoc
    @tree.command(
        name="geek",
        description="Otwiera panel pomocy z wyborem kategorii",
        guild=guild
    )
    async def geek(interaction: discord.Interaction):
        await send_help(interaction)
        
    # Add help as an alias for pomoc
    @tree.command(
        name="help",
        description="Otwiera panel pomocy z wyborem kategorii",
        guild=guild
    )
    async def help(interaction: discord.Interaction):
        await send_help(interaction)

    # Slash command: changelog
    @tree.command(
        name="changelog",
        description="Wyświetla najnowsze zmiany w bocie",
        guild=guild
    )
    async def changelog(interaction: discord.Interaction):
        await interaction.response.send_message(embed=create_changelog_embed())
