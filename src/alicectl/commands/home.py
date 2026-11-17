import logging

from ..api.iot import Capability, IotDevice
from ..context import Context
from ..errors import AlicectlError
from ..lookup import pick
from ..options import AccountOptions, ScenarioOptions, SetOptions, TurnOptions
from .base import one_device, table

log = logging.getLogger(__name__)

SETTABLE_KINDS = {"range", "mode", "toggle"}


def state_summary(device: IotDevice) -> str:
    parts = []
    for c in device.capabilities:
        match c.kind, c.value:
            case "on_off", bool(on):
                parts.insert(0, "on" if on else "off")
            case "range" | "mode", value if value is not None:
                parts.append(f"{c.instance} {value}")
            case "toggle", True:
                parts.append(c.instance)
    return ", ".join(parts)


async def cmd_home(ctx: Context, args: AccountOptions) -> None:
    devices = await (await ctx.home()).devices()
    rows = [("NAME", "ROOM", "TYPE", "STATE", "ID")]
    rows += [(d.name, d.room, d.short_type, state_summary(d), d.id) for d in devices]
    table(rows)


def describe_capability(c: Capability) -> str:
    params = c.parameters or {}
    match c.kind:
        case "range" if r := params.get("range"):
            return f"{c.instance} ({r.get('min')}-{r.get('max')})"
        case "mode":
            return f"{c.instance} ({'/'.join(m.get('value', '?') for m in params.get('modes', []))})"
        case "toggle":
            return f"{c.instance} (on/off)"
    return c.instance


def capability_value(c: Capability, text: str) -> object:
    params = c.parameters or {}
    match c.kind:
        case "range":
            try:
                value = float(text)
            except ValueError:
                raise AlicectlError(f"{c.instance} needs a number") from None
            low, high = (params.get("range") or {}).get("min"), (params.get("range") or {}).get("max")
            if (low is not None and value < low) or (high is not None and value > high):
                raise AlicectlError(f"{c.instance} must be {low}-{high}")
            return int(value) if value.is_integer() else value
        case "mode":
            modes = [m.get("value") for m in params.get("modes", [])]
            if text not in modes:
                raise AlicectlError(f"{c.instance} must be one of: {', '.join(map(str, modes))}")
            return text
        case _:
            if text not in {"on", "off"}:
                raise AlicectlError(f"{c.instance} must be on or off")
            return text == "on"


async def cmd_turn(ctx: Context, args: TurnOptions) -> None:
    home = await ctx.home()
    device = await one_device(home, args.device)
    if not (switch := next((c for c in device.capabilities if c.kind == "on_off"), None)):
        raise AlicectlError(f"{device.name} can't be switched on or off")
    await home.act(device, (*switch.action, args.state == "on"))
    log.info(f"{device.name}: {args.state}")


async def cmd_set(ctx: Context, args: SetOptions) -> None:
    home = await ctx.home()
    device = await one_device(home, args.device)
    settable = {c.instance: c for c in device.capabilities if c.kind in SETTABLE_KINDS and c.instance}
    if not (capability := settable.get(args.what)):
        options = ", ".join(map(describe_capability, settable.values())) or "nothing"
        raise AlicectlError(f"{device.name} has no {args.what!r}; it supports: {options}")
    await home.act(device, (*capability.action, capability_value(capability, args.value)))
    log.info(f"{device.name}: {args.what} {args.value}")


async def cmd_scenarios(ctx: Context, args: AccountOptions) -> None:
    scenarios = await (await ctx.home()).scenarios()
    rows = [("NAME", "ACTIVE", "ID")]
    rows += [(s.name, "yes" if s.active else "no", s.id) for s in scenarios]
    table(rows)


async def cmd_scenario(ctx: Context, args: ScenarioOptions) -> None:
    home = await ctx.home()
    scenario = pick(await home.scenarios(), args.scenario, lambda s: (s.id, s.name), "scenario")
    if not scenario.executable:
        raise AlicectlError(f"{scenario.name!r} can't be run manually")
    await home.run_scenario(scenario)
    log.info(f"ran {scenario.name!r}")
