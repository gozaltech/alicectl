import logging
from dataclasses import replace
from datetime import date

from ..api.alice import (
    Alarm,
    Reminder,
    cancel_alarms,
    cancel_reminders,
    change_alarm,
    create_alarm,
    create_reminder,
    get_alarm_defaults,
    get_alarms,
    get_reminders,
    melody_sound,
    set_alarm_defaults,
    track_sound,
)
from ..api.iot import IotDevice
from ..context import Context
from ..errors import AlicectlError
from ..options import (
    AccountOptions,
    AlarmAddOptions,
    AlarmDefaultsOptions,
    AlarmListOptions,
    AlarmSoundOptions,
    AlarmSwitchOptions,
    IdsOptions,
    ReminderAddOptions,
    SoundChoiceOptions,
)
from ..schedule import Schedule, describe_days
from .base import one_station, some_stations, table

log = logging.getLogger(__name__)


def describe_sound(sound: dict | None) -> str:
    match sound:
        case {"type": "DefaultSound", "content_id": name}:
            return f"melody {name}"
        case {"type": kind, "content_id": content}:
            return f"{kind.lower()} {content}"
    return ""


def alarm_when(alarm: Alarm) -> str:
    return alarm.date or describe_days(alarm.days)


def reminder_when(reminder: Reminder) -> str:
    return reminder.at.replace("T", " ")[:16] or f"{describe_days(reminder.days)} {reminder.time[:5]}"


async def chosen_sound(ctx: Context, args: SoundChoiceOptions, device: IotDevice) -> dict | None:
    if args.sound:
        track = await (await ctx.music()).search(args.sound, "track")
        log.info(f"alarm sound: {track}")
        return track_sound(track.id)
    if args.melody:
        melodies = (await get_alarm_defaults(await ctx.web(), device)).melodies
        if args.melody not in melodies:
            raise AlicectlError(f"{device.name} has no melody {args.melody!r}; it has: {', '.join(melodies) or 'none'}")
        return melody_sound(args.melody)
    return None


async def station_names(ctx: Context) -> dict[str, str]:
    return {d.quasar_id: d.name for d in await (await ctx.home()).stations()}


async def cmd_alarm_list(ctx: Context, args: AlarmListOptions) -> None:
    home = await ctx.home()
    targets = await some_stations(home, args.station)
    alarms, failed = await get_alarms(await ctx.web(), [d.quasar_id for d in targets])
    names = {d.quasar_id: d.name for d in targets}
    for device_id in failed:
        log.warning(f"{names.get(device_id, device_id)}: couldn't read its alarms (offline?)")
    today = date.today().isoformat()
    current = [a for a in alarms if args.all or not a.date or a.date >= today]
    if hidden := len(alarms) - len(current):
        log.info(f"{hidden} past one-time alarm(s) hidden; --all shows them")
    if not current:
        print("no alarms")
        return
    rows = [("STATION", "TIME", "WHEN", "ON", "NAME", "SOUND", "ID")]
    rows.extend(
        (
            names.get(a.device_id, a.device_id),
            a.time,
            alarm_when(a),
            "yes" if a.enabled else "no",
            a.name,
            describe_sound(a.sound),
            a.id,
        )
        for a in sorted(current, key=lambda a: (names.get(a.device_id, ""), a.date or "~", a.time))
    )
    table(rows)


async def cmd_alarm_add(ctx: Context, args: AlarmAddOptions) -> None:
    schedule = Schedule(args.time, args.date, args.days or ())
    device = await one_station(await ctx.home(), args.chosen)
    sound = await chosen_sound(ctx, args, device)
    on = None if schedule.days else f"{schedule.moment:%Y-%m-%d}"
    await create_alarm(await ctx.web(), device, f"{schedule.clock:%H:%M}", on, schedule.days, args.name or "", sound)
    log.info(f"{device.name}: alarm {schedule}")


async def find_alarms(ctx: Context, ids: list[str]) -> tuple[list[Alarm], dict[str, IotDevice]]:
    stations = {d.quasar_id: d for d in await (await ctx.home()).stations()}
    alarms, _ = await get_alarms(await ctx.web(), list(stations))
    by_id = {a.id: a for a in alarms}
    if missing := [i for i in ids if i not in by_id]:
        raise AlicectlError(f"no alarm with ID {', '.join(missing)} (see 'alarm list')")
    return [by_id[i] for i in ids], stations


async def cmd_alarm_cancel(ctx: Context, args: IdsOptions) -> None:
    alarms, _ = await find_alarms(ctx, args.ids)
    await cancel_alarms(await ctx.web(), alarms)
    log.info(f"cancelled {len(alarms)} alarm(s)")


async def cmd_alarm_switch(ctx: Context, args: AlarmSwitchOptions) -> None:
    alarms, stations = await find_alarms(ctx, args.ids)
    web = await ctx.web()
    for alarm in alarms:
        await change_alarm(web, alarm, stations[alarm.device_id], enabled=args.enabled)
    log.info(f"{'enabled' if args.enabled else 'disabled'} {len(alarms)} alarm(s)")


async def cmd_alarm_sound(ctx: Context, args: AlarmSoundOptions) -> None:
    (alarm,), stations = await find_alarms(ctx, [args.id])
    device = stations[alarm.device_id]
    if not (sound := await chosen_sound(ctx, args, device)):
        raise AlicectlError("give --sound SEARCH or --melody NAME")
    await change_alarm(await ctx.web(), alarm, device, media_settings=sound)
    log.info(f"{device.name}: alarm {alarm.time} now wakes with {describe_sound(sound)}")


async def cmd_alarm_defaults(ctx: Context, args: AlarmDefaultsOptions) -> None:
    web = await ctx.web()
    device = await one_station(await ctx.home(), args.chosen)
    defaults = await get_alarm_defaults(web, device)
    changes: dict = {}
    if args.sound or args.melody:
        changes["sound"] = await chosen_sound(ctx, args, device)
    if args.volume is not None:
        changes["volume"] = args.volume
    if args.gradual is not None:
        changes["gradual"] = args.gradual == "on"
    if changes:
        await set_alarm_defaults(web, device, replace(defaults, **changes))
        defaults = await get_alarm_defaults(web, device)
        log.info(f"{device.name}: alarm defaults updated")
    table(
        [
            ("sound:", describe_sound(defaults.sound) or "standard"),
            ("volume:", f"{defaults.volume}/10"),
            ("gradual increase:", "on" if defaults.gradual else "off"),
            ("melodies:", ", ".join(defaults.melodies) or "none"),
        ]
    )


async def cmd_reminder_list(ctx: Context, args: AccountOptions) -> None:
    if not (reminders := await get_reminders(await ctx.web())):
        print("no reminders")
        return
    names = await station_names(ctx)
    rows = [("WHEN", "TEXT", "STATIONS", "ID")]
    rows += [(reminder_when(r), r.text, ", ".join(names.get(i, i) for i in r.device_ids), r.id) for r in reminders]
    table(rows)


async def cmd_reminder_add(ctx: Context, args: ReminderAddOptions) -> None:
    schedule = Schedule(args.time, args.date, args.days or ())
    targets = await some_stations(await ctx.home(), args.station) if args.station else []
    if not (args.push or targets):
        raise AlicectlError("a reminder needs --station or a push notification (drop --no-push)")
    at = None if schedule.days else f"{schedule.moment:%Y-%m-%dT%H:%M:%S}"
    text, device_ids = " ".join(args.text), [d.quasar_id for d in targets]
    await create_reminder(await ctx.web(), text, at, schedule.days, f"{schedule.clock:%H:%M}:00", device_ids, args.push)
    log.info(f"reminder {text!r} set for {schedule}")


async def cmd_reminder_cancel(ctx: Context, args: IdsOptions) -> None:
    web = await ctx.web()
    by_id = {r.id: r for r in await get_reminders(web)}
    if missing := [i for i in args.ids if i not in by_id]:
        raise AlicectlError(f"no reminder with ID {', '.join(missing)} (see 'reminder list')")
    await cancel_reminders(web, [by_id[i] for i in args.ids])
    log.info(f"cancelled {len(args.ids)} reminder(s)")
