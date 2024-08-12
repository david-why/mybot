import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar, cast

from dotenv import load_dotenv
from interactions import (
    Client,
    ContextMenuContext,
    InputText,
    Intents,
    Message,
    Modal,
    OptionType,
    SlashContext,
    Snowflake,
    TextStyles,
    integration_types,
    message_context_menu,
    slash_command,
    slash_option,
)
from interactions.api.http.route import Route

load_dotenv()

TOKEN = os.environ['BOT_TOKEN']

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


if __name__ == '__main__':
    client.start(TOKEN)
