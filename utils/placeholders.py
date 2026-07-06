"""
Placeholder handling for custom embeds.

Text placeholders ({username}, {user}, {servername}) can appear anywhere in
title/description/author/footer text. Image placeholders ({serveravatar},
{useravatar}) are only meaningful in image fields (author image, footer
image, main image, thumbnail) and must be the *entire* field value.
"""

from __future__ import annotations

import re

import discord

PLACEHOLDER_GUIDE = (
    "**Placeholders you can use in any text field:**\n"
    "`{username}` \u2014 mentions the user\n"
    "`{user}` \u2014 shows the username without mentioning\n"
    "`{servername}` \u2014 the server's name\n\n"
    "**Placeholders for image fields only (author image, footer image, "
    "main image, thumbnail):**\n"
    "`{serveravatar}` \u2014 the server's icon\n"
    "`{useravatar}` \u2014 the user's avatar"
)

AUTORESPONDER_PLACEHOLDER_GUIDE = (
    "**Placeholders you can use in your response:**\n"
    "`{username}` \u2014 mentions the user\n"
    "`{user}` \u2014 shows the username without mentioning\n"
    "`{servername}` \u2014 the server's name\n\n"
    "**Want to attach one of your saved embeds?**\n"
    "Just drop `{embed:name}` anywhere in your response, e.g. `{embed:welcome}`, "
    "and that embed will be sent along with your message automatically."
)

STICKY_PLACEHOLDER_GUIDE = (
    "**Placeholders you can use in your sticky message:**\n"
    "`{username}` \u2014 mentions whoever just triggered a repost\n"
    "`{user}` \u2014 shows their username without mentioning\n"
    "`{servername}` \u2014 the server's name\n\n"
    "**Want to attach one of your saved embeds?**\n"
    "Just drop `{embed:name}` anywhere in your message, e.g. `{embed:rules}`, "
    "and that embed will be sent along with the sticky automatically."
)

EVENT_PLACEHOLDER_GUIDE = (
    "**Placeholders you can use in your message:**\n"
    "`{username}` \u2014 mentions the member\n"
    "`{servername}` \u2014 the server's name\n\n"
    "**Want to attach one of your saved embeds?**\n"
    "Just drop `{embed:name}` anywhere in your message, e.g. `{embed:welcome}`, "
    "and that embed will be sent along with it automatically."
)

TEXT_PLACEHOLDERS = ("{username}", "{user}", "{servername}")
IMAGE_PLACEHOLDERS = ("{serveravatar}", "{useravatar}")

_EMBED_REF_PATTERN = re.compile(r"\{embed:([a-zA-Z0-9_\-]+)\}")


def extract_embed_reference(text: str | None) -> tuple[str, str | None]:
    """
    Pull a {embed:name} reference out of an autoresponder's response text.

    Returns (text_with_the_token_removed, embed_name_or_None). Only the
    first reference is honored if more than one is present.
    """
    if not text:
        return "", None

    match = _EMBED_REF_PATTERN.search(text)
    if not match:
        return text, None

    name = match.group(1)
    cleaned = _EMBED_REF_PATTERN.sub("", text, count=1).strip()
    return cleaned, name


def resolve_text(text: str | None, member: discord.Member, guild: discord.Guild) -> str | None:
    """Replace text placeholders. Safe to call on any string field."""
    if not text:
        return text

    replacements = {
        "{username}": member.mention,
        "{user}": str(member.display_name),
        "{servername}": guild.name,
    }
    for token, value in replacements.items():
        text = text.replace(token, value)
    return text


def resolve_image(value: str | None, member: discord.Member, guild: discord.Guild) -> str | None:
    """
    Resolve an image field. If the whole field is exactly a supported image
    placeholder, swap it for the matching URL. Otherwise treat it as a
    literal URL (after also resolving any text placeholders, in case
    someone builds a dynamic URL, though that's an edge case).
    """
    if not value:
        return None

    stripped = value.strip()
    if stripped == "{serveravatar}":
        return guild.icon.url if guild.icon else None
    if stripped == "{useravatar}":
        return member.display_avatar.url

    return resolve_text(value, member, guild)


def build_preview_embed(draft: dict, member: discord.Member, guild: discord.Guild) -> discord.Embed:
    """Build a live preview of the embed being edited, with placeholders resolved."""
    color = draft.get("color")
    embed = discord.Embed(
        title=resolve_text(draft.get("title"), member, guild) or None,
        description=resolve_text(draft.get("description"), member, guild) or None,
        color=color if color is not None else discord.Color.blurple(),
    )

    author_text = resolve_text(draft.get("author_text"), member, guild)
    if author_text:
        embed.set_author(
            name=author_text,
            icon_url=resolve_image(draft.get("author_image"), member, guild),
        )

    footer_text = resolve_text(draft.get("footer_text"), member, guild)
    if footer_text:
        embed.set_footer(
            text=footer_text,
            icon_url=resolve_image(draft.get("footer_image"), member, guild),
        )

    image_url = resolve_image(draft.get("image"), member, guild)
    if image_url:
        embed.set_image(url=image_url)

    thumb_url = resolve_image(draft.get("thumbnail"), member, guild)
    if thumb_url:
        embed.set_thumbnail(url=thumb_url)

    if draft.get("timestamp"):
        embed.timestamp = discord.utils.utcnow()

    if not embed.title and not embed.description and not embed.fields:
        embed.description = "*(Nothing set yet \u2014 use the buttons below to edit this embed.)*"

    return embed
