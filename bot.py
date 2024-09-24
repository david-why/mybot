import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar, cast

import aiohttp
from dotenv import load_dotenv
from interactions import (
    TYPE_MESSAGEABLE_CHANNEL,
    AllowedMentions,
    Button,
    ButtonStyle,
    ChannelType,
    Client,
    ContextMenuContext,
    GuildChannel,
    GuildText,
    InputText,
    Intents,
    Member,
    Message,
    Modal,
    OptionType,
    OverwriteType,
    SlashContext,
    Snowflake,
    TextStyles,
    User,
    component_callback,
    contexts,
    integration_types,
    message_context_menu,
    slash_command,
    slash_option,
    user_context_menu,
)
from interactions.api.http.route import Route

load_dotenv()

TOKEN = os.environ['BOT_TOKEN']

CLIENT_ID = os.environ['CLIENT_ID']
CLIENT_SECRET = os.environ['CLIENT_SECRET']

BASE = Path(__file__).parent
DT_PATTERN = re.compile(
    r'\{[~]?(?:.*?!)?(?:(?:(\d{4})/)?(\d{1,2})/(\d{1,2})\s*)?(?:(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?\}'
)
EMOJI_PATTERN = re.compile(r'(?<!<)::([a-zA-Z0-9_]+)::')

K = TypeVar('K')
V = TypeVar('V')


class SizedCache(Generic[K, V]):
    def __init__(self, size: int):
        self.size = size
        assert size > 0
        self.list: list[K] = []
        self.values: dict[K, V] = {}

    def __setitem__(self, key: K, value: V):
        if key in self.values:
            self.list.remove(key)
        if len(self.list) >= self.size:
            old = self.list.pop(0)
            del self.values[old]
        self.list.append(key)
        self.values[key] = value

    def __getitem__(self, item: K) -> V:
        return self.values[item]

    def __contains__(self, item: K) -> bool:
        return item in self.values


def get_timezone():
    file = BASE / 'timezone.txt'
    if file.exists():
        with file.open() as f:
            return timezone(timedelta(hours=float(f.read())))
    return timezone.utc


def set_timezone(tz: timezone):
    dt = tz.utcoffset(None).seconds / 3600
    file = BASE / 'timezone.txt'
    with file.open('w') as f:
        f.write(str(dt))


def parse_spec(string: str, timed: bool, dated: bool):
    if string.startswith('~'):
        return ':R'
    if '!' in string:
        typ, _, string = string.partition('!')
        if typ.startswith('t'):
            return ':t'
        if typ.startswith('d'):
            return ':D'
    if timed and dated:
        return ':f'
    if timed and not dated:
        return ':t'
    if not timed and dated:
        return ':D'
    return ''


def dt_replacer(match: re.Match[str]):
    y, m, d, H, M, S = map(lambda x: x if x is None else int(x), match.groups())
    now = datetime.now().astimezone(get_timezone()).replace(second=0)
    for key, val in {
        'year': y,
        'month': m,
        'day': d,
        'hour': H,
        'minute': M,
        'second': S,
    }.items():
        if val is not None:
            now = now.replace(**{key: val})  # type: ignore
    timed = H is not None
    dated = d is not None
    begin, end = match.span()
    spec = parse_spec(match.string[begin + 1 : end - 1], timed, dated)
    return f'<t:{int(now.timestamp())}{spec}>'


def emoji_replacer(match: re.Match[str]):
    name = match.group(1)
    if name in emojis:
        return f'<:{name}:{emojis[name]}>'
    return f':{name}:'


async def update_emojis():
    if datetime.now() - emoji_updated < timedelta(minutes=5):
        return
    route = Route(
        'GET',
        '/applications/{application_id}/emojis',
        application_id=client.app.id,
    )
    data = cast(dict[str, Any], await client.http.request(route))
    for item in data['items']:
        emojis[item['name']] = item['id']


async def make_message(string: str):
    global emojis
    string = string.replace('\\n', '\n')
    string = DT_PATTERN.sub(dt_replacer, string)
    if EMOJI_PATTERN.search(string):
        await update_emojis()
    string = EMOJI_PATTERN.sub(emoji_replacer, string)
    return string


emojis: dict[str, str] = {}
emoji_updated = datetime.fromtimestamp(0)
timestr_cache: SizedCache[Snowflake, str] = SizedCache(100)

client = Client(
    intents=Intents.DEFAULT,
    basic_logging=True,
    logging_level=logging.DEBUG,
    send_command_tracebacks=False,
    proxy_url=os.environ.get('PROXY_URL'),
)


@slash_command('echo', description='Send a message with template substitutions')
@integration_types(guild=True, user=True)
@slash_option(
    'message',
    description='The message template string',
    opt_type=OptionType.STRING,
    required=True,
)
async def echo_command(ctx: SlashContext, message: str):
    contents = await make_message(message)
    message_obj = await ctx.send(contents)
    timestr_cache[message_obj.id] = message


@slash_command('timezone', description='Set your timezone')
@integration_types(guild=True, user=True)
@slash_option(
    'timezone',
    description='The timezone to set',
    opt_type=OptionType.INTEGER,
    required=True,
    min_value=-12,
    max_value=12,
)
async def timezone_command(ctx: SlashContext):
    new_tz = timezone(timedelta(hours=ctx.kwargs['timezone']))
    set_timezone(new_tz)
    await ctx.send(f'Timezone set to {new_tz.tzname(None)}', ephemeral=True)


@message_context_menu('Edit message')
@integration_types(guild=True, user=True)
async def edit_context(ctx: ContextMenuContext):
    message = ctx.target
    assert isinstance(message, Message)
    if message.author.id != client.user.id:
        return await ctx.send('This message is not sent by me!', ephemeral=True)
    value = timestr_cache[message.id] if message.id in timestr_cache else ''
    ipt = InputText(
        label='New message content',
        style=TextStyles.SHORT,
        custom_id='string',
        value=value,
    )
    modal = Modal(ipt, title='Edit message')
    await ctx.send_modal(modal)
    try:
        mctx = await client.wait_for_modal(modal, timeout=600)
    except TimeoutError:
        return
    timestr_cache[message.id] = mctx.responses['string']
    await mctx.edit(message.id, content=await make_message(mctx.responses['string']))


@message_context_menu('Delete message')
@integration_types(guild=True, user=True)
async def delete_context(ctx: ContextMenuContext):
    message = ctx.target
    assert isinstance(message, Message)
    if message.author.id != client.user.id:
        return await ctx.send('This message is not sent by me!', ephemeral=True)
    try:
        await message.delete()
    except:
        return await ctx.send('Failed to delete message!', ephemeral=True)
    await ctx.send('Message deleted!', ephemeral=True)


async def get_user_info(user: User | Member):
    try:
        discr = user.discriminator
    except AttributeError:
        discr = 0
    content = (
        f'User Display Name: {user.display_name}\n'
        f'User ID: {user.id}\n'
        f'Username: {user.username}\n'
        f'Discriminator: {discr}\n'
        f'User avatar: {user.avatar_url}\n'
        f'User flags: {user.public_flags.name}\n'
    )
    if isinstance(user, Member):
        try:
            perms = user.guild_permissions
        except AttributeError:
            perms = '???'
        roles = ' '.join(f'<@&{role}>' for role in user._role_ids)
        content += (
            f'Joined at: {user.joined_at.format("f")}\n'
            f'Permissions: {perms}\n'
            f'Roles: {roles}\n'
        )
    return content.strip()


@user_context_menu('User info')
@integration_types(guild=True, user=True)
async def user_info(ctx: ContextMenuContext):
    user = ctx.target
    assert isinstance(user, (User, Member))
    component = Button(
        style=ButtonStyle.PRIMARY, label='Make public', custom_id='public'
    )
    await ctx.send(await get_user_info(user), ephemeral=True, components=component)


@component_callback('public')
async def public_callback(ctx: SlashContext):
    assert ctx.message
    await ctx.send(ctx.message.content, allowed_mentions={'parse': []})


@slash_command('userinfo', description='Get user info')
@integration_types(guild=True, user=True)
@slash_option(
    'user',
    description='The user to get info',
    opt_type=OptionType.USER,
    required=False,
)
@slash_option(
    'ephemeral',
    description='Whether to make the message ephemeral',
    opt_type=OptionType.BOOLEAN,
    required=False,
)
async def userinfo_command(
    ctx: SlashContext, user: User | Member | None = None, ephemeral: bool = True
):
    if user is None:
        user = ctx.author
    component = Button(
        style=ButtonStyle.PRIMARY, label='Make public', custom_id='public'
    )
    await ctx.send(
        await get_user_info(user),
        ephemeral=ephemeral,
        allowed_mentions={'parse': []},
        components=component if ephemeral else None,
    )


async def get_channel_info(channel: GuildChannel):
    channel_type = channel.type
    if isinstance(channel.type, ChannelType):
        channel_type = channel.type.name
    content = (
        f'Channel ID: {channel.id}\n'
        f'Channel Name: {channel.name}\n'
        f'Channel Type: {channel_type}\n'
        f'Guild ID: {channel._guild_id}\n'
        f'Created at: {channel.created_at.format("f")}\n'
        f'NSFW: {channel.nsfw}\n'
        f'Position: {channel.position}\n'
    )
    if isinstance(channel, GuildText):
        content += (
            f'Topic: {channel.topic}\n' f'Slowmode: {channel.rate_limit_per_user}\n'
        )
    if not channel.permission_overwrites:
        content += 'No permission overwrites\n'
    else:
        content += 'Permission overwrites:\n'
        for overwrite in channel.permission_overwrites:
            mention = (
                f'<@{overwrite.id}>'
                if overwrite.type == OverwriteType.MEMBER
                else f'<@&{overwrite.id}>'
            )
            content += f'  {overwrite.type.name} {mention}'
            if overwrite.allow is not None:
                content += f' | Allow {overwrite.allow.name}'
            if overwrite.deny is not None:
                content += f' | Deny {overwrite.deny.name}'
            content += '\n'
    return content.strip()


@slash_command('channelinfo', description='Get channel info')
@integration_types(guild=True, user=True)
@contexts(bot_dm=False)
@slash_option(
    'channel',
    description='The channel to get info',
    opt_type=OptionType.CHANNEL,
    required=False,
)
@slash_option(
    'ephemeral',
    description='Whether to make the message ephemeral',
    opt_type=OptionType.BOOLEAN,
    required=False,
)
async def channelinfo_command(
    ctx: SlashContext, channel: GuildChannel | None = None, ephemeral: bool = True
):
    for key in dir(channel):
        try:
            value = getattr(channel, key)
        except:
            continue
        print(f'{key}: {value}')
    if channel is None:
        if ctx.guild_id is None:
            return await ctx.send(
                'This command must be used in a guild!', ephemeral=True
            )
        channel = cast(GuildChannel, ctx.channel)
    component = Button(
        style=ButtonStyle.PRIMARY, label='Make public', custom_id='public'
    )
    await ctx.send(
        await get_channel_info(channel),
        ephemeral=ephemeral,
        allowed_mentions={'parse': []},
        components=component if ephemeral else None,
    )


if __name__ == '__main__':
    client.start(TOKEN)
