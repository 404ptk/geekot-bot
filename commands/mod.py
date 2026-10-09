import discord
import json
import os
import logging
from datetime import datetime
from config.conf_permissions_settings import require_moderator_command

GUILD_ID = 551503797067710504
ARCHIVE_CATEGORY_ID = 1360605748186452110
CHANNEL_PRIVACY_FILE = "txt/channel_privacy_settings.json"
logger = logging.getLogger(__name__)

def load_channel_privacy():
    """Ładuje ustawienia prywatności kanałów"""
    try:
        if os.path.exists(CHANNEL_PRIVACY_FILE):
            with open(CHANNEL_PRIVACY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Błąd przy ładowaniu ustawień prywatności kanałów: {e}")
    return {}


def save_channel_privacy(privacy_data):
    """Zapisuje ustawienia prywatności kanałów"""
    try:
        os.makedirs(os.path.dirname(CHANNEL_PRIVACY_FILE) or '.', exist_ok=True)
        with open(CHANNEL_PRIVACY_FILE, 'w', encoding='utf-8') as f:
            json.dump(privacy_data, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Błąd przy zapisywaniu ustawień prywatności kanałów: {e}")
        return False


def extract_channel_privacy(channel: discord.TextChannel) -> dict:
    """Ekstrahuje ustawienia prywatności z kanału"""
    privacy_settings = {
        "channel_id": channel.id,
        "channel_name": channel.name,
        "saved_at": datetime.now().isoformat(),
        "category_id": channel.category_id,
        "permissions_synced": channel.permissions_synced,
        "position": channel.position,
        "default_auto_archive_duration": channel.default_auto_archive_duration,
        "default_thread_slowmode_delay": channel.default_thread_slowmode_delay,
        "permissions_overwrites": {},
        "topic": channel.topic,
        "slowmode_delay": channel.slowmode_delay,
        "nsfw": channel.nsfw,
    }
    
    # Zapisz wszystkie permission overwrites
    for target, permissions in channel.overwrites.items():
        target_type = "role" if isinstance(target, discord.Role) else "member"
        target_name = target.name if hasattr(target, "name") else str(target.id)
        
        privacy_settings["permissions_overwrites"][str(target.id)] = {
            "type": target_type,
            "name": target_name,
            "allow": permissions.pair()[0].value,
            "deny": permissions.pair()[1].value,
        }
    
    return privacy_settings

async def setup_mod_commands(client: discord.Client, tree: discord.app_commands.CommandTree, guild_id: int = None):
    guild = discord.Object(id=guild_id) if guild_id else discord.Object(id=GUILD_ID)

    # --- Zamknij kanał ---
    @tree.command(
        name="zamknij",
        description="Przenosi kanał do archiwum i blokuje pisanie",
        guild=guild
    )
    @discord.app_commands.describe(
        kanal="Kanał do zamknięcia (jeśli nie podasz, zamknie bieżący)"
    )
    async def zamknij(
        interaction: discord.Interaction,
        kanal: discord.TextChannel = None
    ):
        if not await require_moderator_command(interaction, "zamknij"):
            return

        channel = kanal or interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Ta komenda działa tylko na kanałach tekstowych.", ephemeral=True
            )
            return

        category = discord.utils.get(interaction.guild.categories, id=ARCHIVE_CATEGORY_ID)
        if category is None:
            await interaction.response.send_message(
                f"Nie znaleziono kategorii o ID {ARCHIVE_CATEGORY_ID}.",
                ephemeral=True
            )
            return

        if channel.category_id == ARCHIVE_CATEGORY_ID:
            await interaction.response.send_message(
                "Kanał jest już w archiwum. Nie nadpisuję jego zapisanych ustawień.",
                ephemeral=True,
            )
            return

        # Zapisz ustawienia prywatności kanału
        privacy_data = load_channel_privacy()
        channel_settings = extract_channel_privacy(channel)
        privacy_data[str(channel.id)] = channel_settings
        if not save_channel_privacy(privacy_data):
            await interaction.response.send_message(
                "Nie udało się zapisać ustawień prywatności. Kanał nie został zamknięty.",
                ephemeral=True,
            )
            return

        # Preserve all visibility rules, including overwrites synced from the
        # original category. Keyword-only set_permissions would replace the
        # entire @everyone overwrite and remove its view_channel deny.
        overwrites = channel.overwrites
        for overwrite in overwrites.values():
            overwrite.send_messages = False
            overwrite.send_messages_in_threads = False
            overwrite.create_public_threads = False
            overwrite.create_private_threads = False
        everyone = interaction.guild.default_role
        everyone_overwrite = overwrites.get(everyone, discord.PermissionOverwrite())
        everyone_overwrite.send_messages = False
        everyone_overwrite.send_messages_in_threads = False
        everyone_overwrite.create_public_threads = False
        everyone_overwrite.create_private_threads = False
        overwrites[everyone] = everyone_overwrite
        await channel.edit(category=category, sync_permissions=False, overwrites=overwrites)
        await interaction.response.send_message(
            f"Kanał {channel.mention} został przeniesiony do kategorii **{category.name}** i zablokowano możliwość pisania.\n✅ Ustawienia prywatności kanału zostały zapisane."
        )

    @tree.command(
        name="otworz",
        description="Przywraca kanał z archiwum wraz z zapisanymi ustawieniami",
        guild=guild,
    )
    @discord.app_commands.describe(
        kanal="Kanał do otwarcia (jeśli nie podasz, otworzy bieżący)"
    )
    async def otworz(interaction: discord.Interaction, kanal: discord.TextChannel = None):
        if not await require_moderator_command(interaction, "otworz"):
            return
        channel = kanal or interaction.channel

        def log_issue(code, detail):
            logger.warning(
                "[otworz:%s] guild=%s channel=%s actor=%s %s",
                code, interaction.guild.id, getattr(channel, "id", None), interaction.user.id, detail,
            )

        if not isinstance(channel, discord.TextChannel):
            log_issue("unsupported_channel", "Kanał nie jest kanałem tekstowym.")
            await interaction.response.send_message(
                "Ta komenda działa tylko na kanałach tekstowych.", ephemeral=True
            )
            return
        if channel.category_id != ARCHIVE_CATEGORY_ID:
            log_issue("already_open", "Kanał nie znajduje się w archiwum.")
            await interaction.response.send_message(
                "Kanał nie jest w archiwum. Nie nadpisuję jego aktualnych ustawień.", ephemeral=True
            )
            return
        settings = load_channel_privacy().get(str(channel.id))
        if not isinstance(settings, dict) or not isinstance(settings.get("permissions_overwrites"), dict):
            log_issue("missing_permissions", "Brak prawidłowego zapisu uprawnień; przerwano przywracanie.")
            await interaction.response.send_message(
                "Nie można otworzyć kanału: brak zapisu uprawnień. Kanał nie został zmieniony.",
                ephemeral=True,
            )
            return
        warnings = []
        category_id = settings.get("category_id")
        category = discord.utils.get(interaction.guild.categories, id=category_id) if category_id is not None else None
        if "category_id" not in settings:
            warnings.append("W zapisie brakuje pierwotnej kategorii — kanał umieszczono poza kategoriami.")
            log_issue("missing_category_record", warnings[-1])
        elif category_id is not None and category is None:
            warnings.append(f"Kategoria {category_id} już nie istnieje — kanał umieszczono poza kategoriami.")
            log_issue("deleted_category", warnings[-1])

        await interaction.response.defer(ephemeral=True)
        overwrites = {}
        for target_id, saved in settings["permissions_overwrites"].items():
            try:
                if saved["type"] not in {"role", "member"}:
                    raise ValueError("Nieznany typ odbiorcy uprawnień")
                restored = discord.PermissionOverwrite.from_pair(
                    discord.Permissions(saved["allow"]), discord.Permissions(saved["deny"])
                )
                target_number = int(target_id)
            except (KeyError, TypeError, ValueError) as exc:
                log_issue("invalid_permission_record", f"target={target_id} error={exc}")
                await interaction.followup.send(
                    "Zapis uprawnień jest uszkodzony. Kanał nie został zmieniony; szczegóły w logu bota.", ephemeral=True
                )
                return
            if saved["type"] == "role":
                target = interaction.guild.get_role(target_number)
            else:
                target = interaction.guild.get_member(target_number)
                if target is None:
                    try:
                        target = await interaction.guild.fetch_member(target_number)
                    except discord.NotFound:
                        target = None
                    except discord.HTTPException as exc:
                        log_issue("member_lookup_failed", f"target={target_id} error={exc}")
                        await interaction.followup.send(
                            "Nie udało się odczytać zapisanych uprawnień użytkownika. Kanał nie został zmieniony.", ephemeral=True
                        )
                        return
            if target is None:
                kind = "roli" if saved["type"] == "role" else "użytkownika"
                warnings.append(f"Nie znaleziono {kind} {saved.get('name', target_id)} (ID {target_id}) — pominięto jej/jego uprawnienia.")
                log_issue("missing_role" if saved["type"] == "role" else "missing_member", warnings[-1])
                continue
            overwrites[target] = restored

        # Restore the snapshot, not the category's potentially changed rules.
        options = {
            "category": category,
            "sync_permissions": False,
            "overwrites": overwrites,
        }
        for key in ("channel_name", "topic", "slowmode_delay", "nsfw", "position", "default_auto_archive_duration", "default_thread_slowmode_delay"):
            if key in settings:
                options["name" if key == "channel_name" else key] = settings[key]
            else:
                warnings.append(f"Brak zapisanego ustawienia `{key}` — pozostawiono obecną wartość.")
                log_issue("missing_setting", f"setting={key}")
        try:
            await channel.edit(**options)
        except (discord.HTTPException, ValueError, TypeError) as exc:
            log_issue("restore_failed", f"error={type(exc).__name__}: {exc}")
            await interaction.followup.send(
                "Discord odrzucił przywracanie kanału. Sprawdź uprawnienia bota i aktualne ustawienia kanału; zapis został zachowany.",
                ephemeral=True,
            )
            return
        message = f"Kanał {channel.mention} został otwarty."
        if warnings:
            message += "\n⚠️ Przywrócono dostępne ustawienia, ale nie wszystko udało się odtworzyć:\n" + "\n".join(f"• {w}" for w in warnings[:8])
            if len(warnings) > 8:
                message += f"\n• Pozostałe ostrzeżenia: {len(warnings) - 8}. Szczegóły w logu bota."
        else:
            message += " Przywrócono pierwotną kategorię i zapisane ustawienia kanału."
        logger.info("[otworz:restored] guild=%s channel=%s actor=%s warnings=%s", interaction.guild.id, channel.id, interaction.user.id, len(warnings))
        await interaction.followup.send(
            message[:1900],
            ephemeral=True,
        )

    # --- Czyszczenie wiadomości ---
    @tree.command(
        name="czysc",
        description="Usuwa ostatnie wiadomości na bieżącym kanale",
        guild=guild,
    )
    @discord.app_commands.describe(
        liczba="Ile wiadomości usunąć (1-100)",
        uzytkownik="Usuń ostatnie wiadomości tego użytkownika (opcjonalnie)",
    )
    async def czysc(
        interaction: discord.Interaction,
        liczba: discord.app_commands.Range[int, 1, 100],
        uzytkownik: discord.Member = None,
    ):
        if not await require_moderator_command(interaction, "czysc"):
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Ta komenda działa tylko na kanałach tekstowych.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not channel.permissions_for(interaction.guild.me).manage_messages:
            await interaction.response.send_message(
                "Bot nie ma uprawnienia do zarządzania wiadomościami na tym kanale.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        reason = f"Czyszczenie przez {interaction.user} ({interaction.user.id})"
        try:
            if uzytkownik:
                matched = {"count": 0}

                def check(message: discord.Message) -> bool:
                    if message.author.id != uzytkownik.id:
                        return False
                    matched["count"] += 1
                    return matched["count"] <= liczba

                deleted = await channel.purge(limit=1000, check=check, reason=reason)
                await interaction.followup.send(
                    f"Usunięto **{len(deleted)}** wiadomości użytkownika {uzytkownik.mention} na {channel.mention}.",
                    ephemeral=True,
                )
            else:
                deleted = await channel.purge(limit=liczba, reason=reason)
                await interaction.followup.send(
                    f"Usunięto **{len(deleted)}** ostatnich wiadomości na {channel.mention}.",
                    ephemeral=True,
                )
        except discord.Forbidden:
            await interaction.followup.send(
                "Nie udało się usunąć wiadomości — brak uprawnień.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"Nie udało się usunąć wiadomości: {e}",
                ephemeral=True,
            )

    # --- Synchronizacja globalna ---
    @tree.command(
        name="sync",
        description="Synchronizuje komendy slash globalnie",
        guild=guild
    )
    async def sync(interaction: discord.Interaction):
        if not await require_moderator_command(interaction, "sync"):
            return
        try:
            synced = await client.tree.sync()
            await interaction.response.send_message(f"✅ Zsynchronizowano {len(synced)} komend slash globalnie.")
        except Exception as e:
            await interaction.response.send_message(f"❌ Błąd synchronizacji: {e}", ephemeral=True)

    # --- Synchronizacja dla serwera ---
    @tree.command(
        name="guildsync",
        description="Synchronizuje komendy slash tylko dla tego serwera",
        guild=guild
    )
    async def guildsync(interaction: discord.Interaction):
        if not await require_moderator_command(interaction, "guildsync"):
            return
        try:
            synced = await client.tree.sync(guild=discord.Object(id=interaction.guild.id))
            await interaction.response.send_message(f"✅ Zsynchronizowano {len(synced)} komend slash dla tego serwera.")
        except Exception as e:
            await interaction.response.send_message(f"❌ Błąd synchronizacji: {e}", ephemeral=True)

    # --- Czyszczenie komend na serwerze ---
    @tree.command(
        name="clearcmds",
        description="Czyści wszystkie komendy slash z tego serwera",
        guild=guild
    )
    async def clearcmds(interaction: discord.Interaction):
        if not await require_moderator_command(interaction, "clearcmds"):
            return
        client.tree.clear_commands(guild=discord.Object(id=interaction.guild.id))
        await client.tree.sync(guild=discord.Object(id=interaction.guild.id))
        await interaction.response.send_message("Wyczyszczono komendy slash dla tego serwera.")

    # --- Lista globalnych komend slash ---
    @tree.command(
        name="slashlist",
        description="Wyświetla listę globalnych komend slash",
        guild=guild
    )
    async def slashlist(interaction: discord.Interaction):
        if not await require_moderator_command(interaction, "slashlist"):
            return
        cmds = client.tree.get_commands()
        if cmds:
            cmd_names = "\n".join(f"- {cmd.name}" for cmd in cmds)
            await interaction.response.send_message(f"**Globalne komendy slash:**\n{cmd_names}")
        else:
            await interaction.response.send_message("Brak zarejestrowanych globalnych komend slash.")

    # --- Lista komend slash na tym serwerze ---
    @tree.command(
        name="gslashlist",
        description="Wyświetla listę komend slash na tym serwerze",
        guild=guild
    )
    async def gslashlist(interaction: discord.Interaction):
        if not await require_moderator_command(interaction, "gslashlist"):
            return
        cmds = client.tree.get_commands(guild=discord.Object(id=interaction.guild.id))
        if cmds:
            cmd_names = "\n".join(f"- {cmd.name}" for cmd in cmds)
            await interaction.response.send_message(f"**Komendy slash na tym serwerze:**\n{cmd_names}")
        else:
            await interaction.response.send_message("Brak zarejestrowanych komend slash na tym serwerze.")

    # --- Czyszczenie globalnych komend slash ---
    @tree.command(
        name="clearglobalcmds",
        description="Czyści wszystkie globalne komendy slash",
        guild=guild
    )
    async def clearglobalcmds(interaction: discord.Interaction):
        if not await require_moderator_command(interaction, "clearglobalcmds"):
            return
        try:
            client.tree.clear_commands(guild=None)
            synced = await client.tree.sync()
            await interaction.response.send_message("✅ Usunięto wszystkie globalne komendy slash.")
        except Exception as e:
            await interaction.response.send_message(f"❌ Błąd podczas czyszczenia globalnych komend: {e}", ephemeral=True)
