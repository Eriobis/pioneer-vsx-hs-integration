"""Pioneer VSX media player via the pioneer-bridge serial<->TCP bridge.

Keeps one persistent TCP connection to the bridge; the amp pushes PWR/VOL/MUT/FN
lines on every change, so state is instant.

In standby the amp sends nothing (not even after PF) and ANY serial byte wakes it
up. So: PF marks the entity off optimistically, the amp is only polled while it
is on, and liveness uses `#PING`, which the bridge answers without touching the
serial port. Last on/off state is restored across HA restarts.
"""

import asyncio
import logging

import voluptuous as vol

from homeassistant.components.media_player import (
    PLATFORM_SCHEMA as MEDIA_PLAYER_PLATFORM_SCHEMA,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, EVENT_HOMEASSISTANT_STOP
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

_LOGGER = logging.getLogger(__name__)

CONF_SOURCES = "sources"
CONF_MAX_VOLUME = "max_volume"
POLL_SECONDS = 30
QUERIES = ("?P", "?V", "?M", "?F")

PLATFORM_SCHEMA = MEDIA_PLAYER_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=8102): cv.port,
        vol.Optional(CONF_NAME, default="Sound System"): cv.string,
        # Raw amp volume that maps to 100 %; never sent above this.
        vol.Optional(CONF_MAX_VOLUME, default=60): vol.All(int, vol.Range(1, 185)),
        vol.Optional(CONF_SOURCES, default={}): {cv.string: cv.string},
    }
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    entity = PioneerSerial(config)
    async_add_entities([entity])
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, entity.async_shutdown)


class AmpStoredData(ExtraStoredData):
    """Last known amp state, kept even while the entity is unavailable."""

    def __init__(self, data):
        self.data = data

    def as_dict(self):
        return self.data


class PioneerSerial(MediaPlayerEntity, RestoreEntity):
    _attr_should_poll = False
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    def __init__(self, config):
        self._host = config[CONF_HOST]
        self._port = config[CONF_PORT]
        self._max = config[CONF_MAX_VOLUME]
        self._attr_name = config[CONF_NAME]
        self._attr_unique_id = f"pioneer_serial_{self._host}_{self._port}"
        self._name_to_code = {name: f"{int(code):02d}" for name, code in config[CONF_SOURCES].items()}
        self._code_to_name = {c: n for n, c in self._name_to_code.items()}
        self._attr_source_list = list(self._name_to_code)
        self._attr_available = False
        self._raw_volume = None
        self._writer = None
        self._task = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._known = False
        if (extra := await self.async_get_last_extra_data()) and extra.as_dict().get("power") in ("on", "off"):
            d = extra.as_dict()
            self._known = True
            self._attr_state = MediaPlayerState(d["power"])
            self._attr_source = d.get("source")
            self._attr_is_volume_muted = d.get("muted")
            self._raw_volume = d.get("raw_volume")
            if self._raw_volume is not None:
                self._attr_volume_level = min(self._raw_volume / self._max, 1.0)
        else:
            self._attr_state = MediaPlayerState.OFF
        self._task = self.hass.async_create_background_task(self._run(), "pioneer_serial")

    async def async_will_remove_from_hass(self):
        await self.async_shutdown()

    async def async_shutdown(self, *_):
        if self._task:
            self._task.cancel()
            self._task = None
        if self._writer:
            self._writer.close()

    async def _run(self):
        backoff = 1
        while True:
            poller = None
            try:
                reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port), 10)
                _LOGGER.info("Connected to pioneer-bridge %s:%s", self._host, self._port)
                backoff = 1
                self._attr_available = True
                self.async_write_ha_state()
                if not self._known:
                    # First run ever: one query to learn the state (wakes the amp if in standby).
                    for q in QUERIES:
                        await self._send(q)
                    self._known = True
                poller = asyncio.create_task(self._poll())
                while True:
                    # #PONG every poll cycle; silence longer than that means a dead link.
                    line = await asyncio.wait_for(reader.readline(), POLL_SECONDS * 2 + 10)
                    if not line:
                        raise ConnectionError("bridge closed connection")
                    # Wake-up from standby prefixes a garbage byte (e.g. b"\xffPWR0"); keep printable ASCII only.
                    self._handle(bytes(b for b in line if 32 <= b < 127).decode("ascii").strip())
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("pioneer-bridge %s:%s: %s; retrying in %ss",
                                self._host, self._port, err, backoff)
            finally:
                if poller:
                    poller.cancel()
                if self._writer:
                    self._writer.close()
                    self._writer = None
            self._attr_available = False
            self.async_write_ha_state()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _poll(self):
        while True:
            await self._send("#PING")
            if self._attr_state == MediaPlayerState.ON:
                for q in QUERIES:
                    await self._send(q)
            await asyncio.sleep(POLL_SECONDS)

    async def _send(self, cmd):
        if self._writer is None:
            _LOGGER.warning("Not connected, dropping %s", cmd)
            return
        self._writer.write(cmd.encode("ascii") + b"\r")
        await self._writer.drain()

    def _handle(self, line):
        if line == "#PONG":
            return
        if line.startswith("PWR"):
            # PWR0 = on, PWR1/PWR2 = standby
            self._attr_state = MediaPlayerState.ON if line == "PWR0" else MediaPlayerState.OFF
        elif line.startswith("VOL") and line[3:].isdigit():
            self._raw_volume = int(line[3:])
            self._attr_state = MediaPlayerState.ON  # amp is silent in standby
            self._attr_volume_level = min(self._raw_volume / self._max, 1.0)
        elif line.startswith("MUT"):
            self._attr_is_volume_muted = line == "MUT0"
        elif line.startswith("FN") and line[2:].isdigit():
            code = line[2:]
            self._attr_source = self._code_to_name.get(code, f"Input {code}")
        else:
            _LOGGER.debug("Unhandled from amp: %s", line)
            return
        self.async_write_ha_state()

    @property
    def extra_restore_state_data(self):
        return AmpStoredData({
            "power": self._attr_state.value if self._attr_state else None,
            "source": self._attr_source,
            "muted": self._attr_is_volume_muted,
            "raw_volume": self._raw_volume,
        })

    @property
    def extra_state_attributes(self):
        return {"raw_volume": self._raw_volume, "max_volume": self._max}

    async def async_turn_on(self):
        await self._send("PO")

    async def async_turn_off(self):
        await self._send("PF")
        self._attr_state = MediaPlayerState.OFF  # amp never reports standby
        self.async_write_ha_state()

    async def async_volume_up(self):
        if self._raw_volume is None or self._raw_volume < self._max:
            await self._send("VU")

    async def async_volume_down(self):
        await self._send("VD")

    async def async_set_volume_level(self, volume):
        raw = max(0, min(round(volume * self._max), self._max))
        await self._send(f"{raw:03d}VL")

    async def async_mute_volume(self, mute):
        await self._send("MO" if mute else "MF")

    async def async_select_source(self, source):
        code = self._name_to_code.get(source)
        if code is None:
            _LOGGER.error("Unknown source %s", source)
            return
        await self._send(f"{code}FN")
