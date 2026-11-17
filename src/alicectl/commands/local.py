from argparse import ArgumentTypeError

from ..api.music import WAVE, MusicItem
from ..api.quasar import Device
from ..context import Context
from ..control.actions import SEEK_STEP, STATUS, TRACK_STEPS, Act, ask, play, player, say, seek, skip, timer, volume
from ..control.state import State
from ..control.stations import Show, Stations, ok
from ..errors import AlicectlError
from ..options import (
    AskOptions,
    OpinionOptions,
    PlaybackOptions,
    PlayOptions,
    StationsOptions,
    TextOptions,
    TimerOptions,
    VolumeOptions,
)
from ..schedule import count_arg, position_arg, step_arg

OPINION_RESULTS = {
    ("like", False): ("liked {}", "already liked {}"),
    ("like", True): ("unliked {}", "{} wasn't liked"),
    ("dislike", False): ("disliked {}", "already disliked {}"),
    ("dislike", True): ("undisliked {}", "{} wasn't disliked"),
}


def saying(text: str) -> Show:
    async def show(_: Device, __: State) -> str:
        return text

    return show


async def describe(_: Device, state: State) -> str:
    return state.describe()


async def landed(_: Device, state: State) -> str:
    return f"now: {state.track}" if state.track and not state.cloud else "ok"


async def show_volume(_: Device, state: State) -> str:
    return str(state.volume) if state.volume is not None else "unknown"


async def on_stations(ctx: Context, args: StationsOptions, act: Act, show: Show = ok) -> None:
    await Stations(ctx, args.stations).each(act, show)


async def cmd_say(ctx: Context, args: TextOptions) -> None:
    await on_stations(ctx, args, say(args.joined))


async def cmd_ask(ctx: Context, args: AskOptions) -> None:
    await on_stations(ctx, args, ask(args.joined, args.quiet))


async def cmd_status(ctx: Context, args: StationsOptions) -> None:
    await on_stations(ctx, args, STATUS, describe)


async def cmd_volume(ctx: Context, args: VolumeOptions) -> None:
    if args.level is None:
        await on_stations(ctx, args, STATUS, show_volume)
    else:
        await on_stations(ctx, args, volume(args.level))


def player_act(action: str, amount: str | None) -> Act:
    try:
        match action, amount:
            case "next" | "prev", _:
                return player(action, count_arg(amount or "1"))
            case "forward" | "rewind", _:
                return skip(step_arg(amount) if amount else SEEK_STEP, forward=action == "forward")
            case "seek", str():
                return seek(position_arg(amount))
            case "seek", None:
                raise AlicectlError("seek where to? e.g. 1:30 or 90")
            case _, None:
                return player(action)
            case _:
                raise AlicectlError(f"{action} doesn't take an amount")
    except ArgumentTypeError as e:
        raise AlicectlError(f"{action} {amount}: {e}") from None


async def cmd_player(ctx: Context, args: PlaybackOptions) -> None:
    show = landed if args.action in TRACK_STEPS else ok
    await on_stations(ctx, args, player_act(args.action, args.amount), show)


async def cmd_timer(ctx: Context, args: TimerOptions) -> None:
    act = timer(args.duration, args.quiet)
    await on_stations(ctx, args, act, saying(act.command))


async def chosen_music(ctx: Context, args: PlayOptions) -> MusicItem:
    if args.wave:
        return MusicItem("radio", WAVE, "Моя волна")
    music = await ctx.music()
    if args.likes:
        return await music.likes()
    if args.my_playlist:
        return await music.playlist(args.my_playlist)
    if not args.query:
        raise AlicectlError("what should it play? give a search, --likes, --my-playlist or --wave")
    return await music.search(" ".join(args.query), args.kind)


async def cmd_play(ctx: Context, args: PlayOptions) -> None:
    item = await chosen_music(ctx, args)
    await on_stations(ctx, args, play(item, args.shuffle, args.repeat), saying(f"playing {item}"))


async def cmd_opinion(ctx: Context, args: OpinionOptions) -> None:
    music, stations = await ctx.music(), Stations(ctx, args.stations)

    async def mark(device: Device, state: State) -> str:
        if not state.track_id:
            raise AlicectlError("nothing is playing")
        changed = await music.mark(state.track_id, args.opinion, args.undo)
        if args.opinion == "dislike" and not args.undo:
            await stations.run(device, player("next"))
        done, unchanged = OPINION_RESULTS[args.opinion, args.undo]
        return (done if changed else unchanged).format(state.track)

    await stations.each(STATUS, mark)
