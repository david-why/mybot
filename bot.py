import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from interactions import (
    Client,
    ContextMenuContext,
    InputText,
    Intents,
    Message,
    MessageType,
    Modal,
    OptionType,
    SlashContext,
    TextStyles,
    integration_types,
    message_context_menu,
    slash_command,
    slash_option,
)

load_dotenv()

TOKEN = os.environ['BOT_TOKEN']

BASE = Path(__file__).parent
DT_PATTERN = re.compile(
    r'\{[~]?(?:.*?!)?(?:(?:(\d{4})/)?(\d{1,2})/(\d{1,2})\s*)?(?:(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?\}'
)


class SizedCache:
    def __init__(self, size: int):
        self.size = size
        assert size > 0
        self.list = []
        self.values = {}

    def __setitem__(self, key, value):
        if key in self.values:
            self.list.remove(key)
        if len(self.list) >= self.size:
            old = self.list.pop(0)
            del self.values[old]
        self.list.append(key)
        self.values[key] = value

    def __getitem__(self, item):
        return self.values[item]

    def __contains__(self, item):
        return item in self.list


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


def make_timestr(string: str):
    return DT_PATTERN.sub(dt_replacer, string)


timestr_cache = SizedCache(100)

client = Client(
    intents=Intents.DEFAULT,
    basic_logging=True,
    logging_level=logging.DEBUG,
    send_command_tracebacks=False,
)


@slash_command('timestr', description='Format a string with timestamps')
@integration_types(guild=True, user=True)
@slash_option(
    'string',
    description='The string with timestamps in it',
    opt_type=OptionType.STRING,
    required=True,
)
async def timestr_command(ctx: SlashContext, string: str):
    message = await ctx.send(make_timestr(string))
    timestr_cache[message.id] = string


@slash_command('timezone', description='Set the timezone for /timestr')
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


@message_context_menu('Edit /timestr message')
@integration_types(guild=True, user=True)
async def timestr_context(ctx: ContextMenuContext):
    message = ctx.target
    assert isinstance(message, Message)
    if (
        message.author.id != client.user.id
        or message.type != MessageType.APPLICATION_COMMAND
    ):
        return await ctx.send('This message is not a /timestr message', ephemeral=True)
    value = timestr_cache[message.id] if message.id in timestr_cache else ''
    ipt = InputText(
        label='/timestr string',
        style=TextStyles.SHORT,
        custom_id='timestr',
        value=value,
    )
    modal = Modal(ipt, title='Edit /timestr message')
    await ctx.send_modal(modal)
    try:
        mctx = await client.wait_for_modal(modal, timeout=600)
    except TimeoutError:
        return
    timestr_cache[message.id] = mctx.responses['timestr']
    await mctx.edit(message.id, content=make_timestr(mctx.responses['timestr']))


client.start(TOKEN)
