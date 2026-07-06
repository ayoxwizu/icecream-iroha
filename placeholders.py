"""
placeholders.py
Replaces the supported placeholder tokens inside a stored template string.

Supported everywhere (plain text or embed):
    {username}   -> mentions the user, e.g. <@123456789>
    {user}       -> the user's account username, no mention/ping
    {channel}    -> mentions the channel, e.g. <#123456789>
    {user_name}  -> the user's display name, no mention/ping
    {process}    -> the name of the process currently being queued
    {order}      -> the order value passed to /queue (any text, e.g. "burger")
    {servername} -> the server's name

Embed-only (resolved to image URLs you can drop into Embed.set_image / set_thumbnail):
    {useravatar}    -> the user's avatar URL
    {serverravatar} -> the guild's icon URL ({serveravatar} also accepted)
"""

import discord


_USER_AVATAR_TOKENS = ("{useravatar}",)
_SERVER_AVATAR_TOKENS = ("{serverravatar}", "{serveravatar}")
_IMAGE_TOKENS = _USER_AVATAR_TOKENS + _SERVER_AVATAR_TOKENS


def _normalize_token(value: str) -> str:
    return value.strip().lower()


def render_text(template: str, *, member: discord.Member, channel: discord.abc.GuildChannel,
                 process_name: str, order: str = "") -> str:
    return (
        template.replace("{username}", member.mention)
        .replace("{user_name}", member.display_name)
        .replace("{user}", member.name)
        .replace("{channel}", channel.mention)
        .replace("{process}", process_name)
        .replace("{order}", str(order))
        .replace("{servername}", member.guild.name)
    )


def get_user_avatar_url(member: discord.Member) -> str:
    return member.display_avatar.url


def get_server_avatar_url(guild: discord.Guild) -> str | None:
    return guild.icon.url if guild.icon else None


def is_valid_image_field(value: str | None) -> bool:
    """
    True if value is empty, one of the special avatar tokens, or a raw
    http(s) URL - i.e. anything resolve_image_field can safely turn into a
    Discord-acceptable icon_url/image url. Discord rejects embed image
    fields that aren't a well-formed http(s) URL, so anything else (a typo,
    a bare domain with no scheme, etc.) must be rejected before it's saved.
    """
    if not value:
        return True
    value = value.strip()
    if _normalize_token(value) in _IMAGE_TOKENS:
        return True
    return value.startswith("http://") or value.startswith("https://")


def resolve_image_field(value: str | None, *, member: discord.Member, guild: discord.Guild) -> str | None:
    """
    Resolves an embed image/icon field that may contain {useravatar},
    {serverravatar} (or the {serveravatar} typo, accepted as an alias), a
    raw URL, or be empty. Falls back to None for anything that isn't a
    well-formed http(s) URL, since Discord rejects malformed icon/image
    URLs with a 400 rather than ignoring them - this keeps a bad value
    already saved in the database from crashing /queue.
    """
    if not value:
        return None
    value = value.strip()
    token = _normalize_token(value)
    if token in _USER_AVATAR_TOKENS:
        return get_user_avatar_url(member)
    if token in _SERVER_AVATAR_TOKENS:
        return get_server_avatar_url(guild)
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return None
