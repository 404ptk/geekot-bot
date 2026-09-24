import asyncio
import io
import json
import logging
import os
from typing import Optional

import discord
from PIL import Image, ImageDraw, ImageFont


FIFA_CHANNEL_ID = 1551247097842503710
FIFA_STATE_FILE = "txt/fifa_state.json"
FIFA_GREEN = "🟢"
FIFA_RED = "🔴"


def _load_font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    filename = "Inter-Bold.ttf" if bold else "Inter-Regular.ttf"
    path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "images", "font", "inter", filename)
    )
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _build_status_image(status: str) -> io.BytesIO:
    is_free = status == "WOLNE"
    background = (35, 135, 72) if is_free else (170, 48, 48)
    width, height = 1600, 900
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    font = _load_font(250)

    left, top, right, bottom = draw.textbbox((0, 0), status, font=font)
    text_width = right - left
    text_height = bottom - top
    draw.text(
        ((width - text_width) // 2 - left, (height - text_height) // 2 - top),
        status,
        fill="white",
        font=font,
    )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _load_state() -> dict:
    try:
        with open(FIFA_STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
            return state if isinstance(state, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(FIFA_STATE_FILE), exist_ok=True)
    temporary_file = f"{FIFA_STATE_FILE}.tmp"
    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
    os.replace(temporary_file, FIFA_STATE_FILE)


async def _delete_message(channel: discord.TextChannel, message_id: Optional[int]) -> None:
    if not message_id:
        return
    try:
        message = await channel.fetch_message(message_id)
        await message.delete()
    except (discord.NotFound, discord.Forbidden):
        pass
    except discord.HTTPException as exc:
        logging.warning("Nie udało się usunąć wiadomości panelu FIFA: %s", exc)


async def setup_fifa_commands(client: discord.Client) -> None:
    channel = client.get_channel(FIFA_CHANNEL_ID)
    if channel is None:
        logging.warning("Nie znaleziono kanału FIFA o ID %s.", FIFA_CHANNEL_ID)
        return
    if not isinstance(channel, discord.TextChannel):
        logging.warning("Kanał FIFA o ID %s nie jest kanałem tekstowym.", FIFA_CHANNEL_ID)
        return

    state = _load_state()
    state_lock = asyncio.Lock()

    async def update_panel(status: str) -> None:
        nonlocal state
        async with state_lock:
            image_message = None
            control_message = None

            image_message_id = state.get("image_message_id")
            control_message_id = state.get("control_message_id")
            if image_message_id and control_message_id:
                try:
                    image_message = await channel.fetch_message(int(image_message_id))
                    control_message = await channel.fetch_message(int(control_message_id))
                    await image_message.edit(
                        attachments=[
                            discord.File(_build_status_image(status), filename="fifa_status.png")
                        ]
                    )
                except discord.NotFound:
                    image_message = None
                    control_message = None

            if image_message is None or control_message is None:
                await _delete_message(channel, image_message_id)
                await _delete_message(channel, control_message_id)

                image_message = await channel.send(
                    file=discord.File(_build_status_image(status), filename="fifa_status.png")
                )
                control_message = await channel.send(
                    "Kliknij reakcję, aby ustawić stan konta:\n"
                    f"{FIFA_GREEN} — konto wolne\n"
                    f"{FIFA_RED} — konto zajęte"
                )
                await control_message.add_reaction(FIFA_GREEN)
                await control_message.add_reaction(FIFA_RED)

            state = {
                "status": status,
                "image_message_id": image_message.id,
                "control_message_id": control_message.id,
            }
            _save_state(state)

    # Przy starcie zachowujemy istniejące wiadomości i aktualizujemy tylko obraz.
    await update_panel(state.get("status", "WOLNE"))

    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
        if payload.channel_id != FIFA_CHANNEL_ID:
            return
        if client.user is not None and payload.user_id == client.user.id:
            return
        if payload.message_id != state.get("control_message_id"):
            return

        emoji = str(payload.emoji)
        if emoji not in (FIFA_GREEN, FIFA_RED):
            try:
                message = await channel.fetch_message(payload.message_id)
                await message.remove_reaction(payload.emoji, discord.Object(id=payload.user_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            return

        new_status = "WOLNE" if emoji == FIFA_GREEN else "ZAJĘTE"
        try:
            message = await channel.fetch_message(payload.message_id)
            await message.remove_reaction(payload.emoji, discord.Object(id=payload.user_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        try:
            await update_panel(new_status)
        except discord.HTTPException as exc:
            logging.error("Nie udało się zaktualizować panelu FIFA: %s", exc)

    client.add_listener(on_raw_reaction_add, "on_raw_reaction_add")
