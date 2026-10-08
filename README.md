# Pioneer VSX RS-232 → Home Assistant

Control an older Pioneer VSX receiver from Home Assistant through its **RS-232
port**. The receiver appears as a regular `media_player` entity with power,
volume, mute and input selection.

> [!NOTE]
> **This project was made with AI.** The code, configuration and this README
> were written by an AI assistant (Claude, by Anthropic, via Claude Code),
> working interactively with the repository owner on their own hardware. It
> has been tested on one receiver (a **Pioneer VSX-9130**) in one home setup.
> Review the code before you run it, and treat it as a starting point rather
> than a polished, widely tested product.

---

## How it works

```
 Pioneer VSX            USB-to-RS-232              Docker host                  Home Assistant
 (RS-232 port) ───────── cable (e.g. PL2303) ───── bridge/  (serial ↔ TCP :8102) ─── custom_components/pioneer_serial
```

There are two parts:

1. **`bridge/`** is a small Docker container that runs on whatever machine the
   USB cable is plugged into. It is the only program that opens the serial port.
   It makes the receiver reachable over the network on TCP port **8102**. Every
   line the receiver sends goes to all connected clients, and every command a
   client sends goes to the receiver.
2. **`custom_components/pioneer_serial/`** is a Home Assistant integration. It
   keeps one connection open to the bridge and turns the receiver's replies
   into a `media_player` entity.

The bridge and Home Assistant don't have to run on the same machine.

## Features

- **Power** on/off (`PO` / `PF`)
- **Volume**: slider, up/down, and a safety cap (`max_volume`) that is never exceeded
- **Mute** on/off
- **Input selection** from a list of names you choose
- **State updates come in over a persistent connection**, so changes made from Home Assistant show up straight away
- **Survives restarts**: the last known power state is restored when Home Assistant restarts, and the bridge restarts itself if the USB cable is unplugged
- **Does not wake the receiver by accident** (see [Why this exists](#why-this-exists))

## Why this exists

Home Assistant has a built-in [`pioneer`](https://www.home-assistant.io/integrations/pioneer/)
integration, but it doesn't work safely with this older receiver:

- **Volume scale.** This receiver reports volume as a 2-digit number
  (`VOL39`, where **dB = value − 81**, so 39 = −42 dB). The built-in integration
  assumes the newer 0–185 scale. On this receiver, 50 % on its slider sends
  `093VL`, which is far louder than intended.
- **Input names.** This receiver answers `?RGB` (the input-name query) with
  `E04`, an error. You have to supply the input names yourself.
- **Standby behaviour.** In standby the receiver says nothing, not even after
  `PF`. **Any byte it receives on the serial port wakes it up.** An integration
  that polls the receiver's status turns it back on within seconds of every
  power-off.

This integration handles all three. It uses a configurable volume scale with a
hard cap. It takes input names from your configuration. It marks the receiver
as off as soon as it sends `PF`, and polls only while the receiver is on. To
check that the connection is alive it sends `#PING`, which the bridge answers
itself without touching the serial port.

## Requirements

- A Pioneer VSX receiver with an **RS-232 port** that speaks the Pioneer ASCII
  protocol at **9600 baud, 8N1**. Tested on a VSX-9130. Other models of the same
  era will probably work, but check their command set.
- A **USB-to-RS-232 cable**. A Prolific PL2303 cable was used here; FTDI,
  CH340 and CP210x cables are also supported by Linux.
- A Linux machine with **Docker and Docker Compose** to run the bridge. It can be
  a server, a Raspberry Pi, or the Home Assistant host if it runs Docker.
- **Home Assistant** 2024.10 or newer (the example automations use the newer `triggers:` / `action:` syntax). This was developed on 2026.9.

## Installation

### 1. Connect the receiver

Plug the USB-to-RS-232 cable into the receiver's RS-232 port and into the
Linux machine. Find the cable's stable device path:

```sh
ls -l /dev/serial/by-id/
# usb-Prolific_Technology_Inc._..._Controller_00000005-if00-port0 -> ../../ttyUSB0
```

Use the `/dev/serial/by-id/...` path rather than `/dev/ttyUSB0`, because the
number can change after a reboot or when the cable is unplugged and plugged back in.

### 2. Run the bridge

```sh
git clone https://github.com/Eriobis/pioneer-vsx-hs-integration.git
cd pioneer-vsx-hs-integration/bridge
```

Edit `compose.yaml` and set the device path on the **left** of the `:` to your
own cable's path:

```yaml
    devices:
      - /dev/serial/by-id/<your-cable>:/dev/ttyUSB0
```

Then build and start it:

```sh
docker compose up -d --build
docker logs pioneer-bridge
# ... INFO bridging /dev/ttyUSB0 @9600 <-> tcp/8102
```

Optional environment variables, to set in `compose.yaml`:

| Variable        | Default        | Meaning                           |
|-----------------|----------------|-----------------------------------|
| `SERIAL_DEVICE` | `/dev/ttyUSB0` | Serial device inside the container |
| `SERIAL_BAUD`   | `9600`         | Baud rate                         |
| `LISTEN_PORT`   | `8102`         | TCP port to listen on             |

> [!WARNING]
> The bridge has **no authentication**. Anyone who can reach port 8102 can
> control the receiver. Keep it on a trusted LAN, and don't forward the port
> to the internet.

### 3. Check the bridge (optional)

**Only do this while the receiver is ON**, because any command wakes it from standby:

```sh
printf '?P\r?V\r' | nc -q1 <bridge-host> 8102
# PWR0      <- on
# VOL39     <- volume
```

### 4. Install the Home Assistant integration

Copy the `custom_components/pioneer_serial` folder into your Home Assistant
config directory so you get:

```
<config>/custom_components/pioneer_serial/__init__.py
<config>/custom_components/pioneer_serial/manifest.json
<config>/custom_components/pioneer_serial/media_player.py
```

Here are some ways to copy it:
- the **File editor**, **Studio Code Server** or **Samba share** add-ons (Home Assistant OS)
- `scp`, if you have SSH access to the config directory
- a bind-mounted config directory (Home Assistant Container)

### 5. Configure it

Add this to `configuration.yaml`. If you already have a `media_player:` section,
add the list item to it instead of creating a second section.

```yaml
media_player:
  - platform: pioneer_serial
    host: 192.168.1.50       # machine running the bridge
    port: 8102
    name: Sound System
    max_volume: 60           # raw value shown as 100 %; never exceeded (60 = -21 dB)
    sources:                 # display name: Pioneer input code
      TV: "05"
      DVD: "04"
      HDMI 1: "19"
      CD: "01"
```

Restart Home Assistant (**Settings → System → Restart**). A restart is
required: Home Assistant only finds new custom integrations at startup, so
reloading YAML isn't enough. The entity appears as `media_player.sound_system`,
based on `name`.

## Configuration reference

| Option       | Required | Default        | Description |
|--------------|----------|----------------|-------------|
| `host`       | yes      |                | Hostname or IP of the machine running the bridge |
| `port`       | no       | `8102`         | Bridge TCP port |
| `name`       | no       | `Sound System` | Entity name |
| `max_volume` | no       | `60`           | Raw receiver volume that counts as 100 %. The integration never sends a higher value, from the slider or the volume-up button. Range 1–185. |
| `sources`    | no       | none           | Map of display name → 2-digit Pioneer input code |

### Volume scale

On the VSX-9130, **dB = raw value − 81**:

| Raw value | Receiver display |
|-----------|------------------|
| 21        | −60 dB           |
| 39        | −42 dB           |
| 51        | −30 dB           |
| 60        | −21 dB           |
| 81        | 0 dB             |

The entity's `volume_level` (0–1) is `raw / max_volume`. The `raw_volume` and
`max_volume` attributes are exposed so automations can work in raw values.
Check one reading against your own receiver's display before trusting this
formula on another model.

### Input codes

These are the standard Pioneer input codes. On the VSX-9130 **only `05` (TV)
has been verified**. Try the others and keep the ones your receiver accepts.
An input the receiver reports that isn't in your list shows up as `Input NN`,
which is an easy way to find a code: switch inputs on the receiver itself and
watch the entity.

| Code | Usual input | Code | Usual input |
|------|-------------|------|-------------|
| `00` | Phono       | `10` | Video 1     |
| `01` | CD          | `14` | Video 2     |
| `02` | Tuner       | `15` | DVR/BDR     |
| `03` | CD-R/Tape   | `17` | iPod/USB    |
| `04` | DVD         | `19` | HDMI 1      |
| `05` | TV/SAT      | `20` | HDMI 2      |
| `06` | SAT/CBL     | `21` | HDMI 3      |
| `25` | BD          |      |             |

## Example automations

### Follow the TV's power

Turn the receiver on (and switch to TV) when the TV turns on. Turn it off when
the TV has been off for 15 s, but only if the receiver is still on the TV input,
so music playing on another input isn't cut off. Replace `media_player.tv` with
your TV's entity: pick one whose on/off state actually follows the screen.
An Android TV Remote or HomeKit entity is usually reliable, while a Cast
entity's `off` only means "not casting".

```yaml
alias: Sound System follows TV
mode: queued
triggers:
  - trigger: state
    entity_id: media_player.tv
    to: "on"
    id: tv_on
  - trigger: state
    entity_id: media_player.tv
    to: "off"
    for: { seconds: 15 }
    id: tv_off
actions:
  - choose:
      - conditions: [{ condition: trigger, id: tv_on }]
        sequence:
          - action: media_player.turn_on
            target: { entity_id: media_player.sound_system }
          - wait_template: "{{ is_state('media_player.sound_system', 'on') }}"
            timeout: { seconds: 15 }
          - delay: { seconds: 2 }
          - action: media_player.select_source
            target: { entity_id: media_player.sound_system }
            data: { source: TV }
      - conditions:
          - condition: trigger
            id: tv_off
          - condition: state
            entity_id: media_player.sound_system
            attribute: source
            state: TV
        sequence:
          - action: media_player.turn_off
            target: { entity_id: media_player.sound_system }
```

### Use the TV remote's volume buttons

If your TV's integration reports `volume_level` (the Android TV Remote one
does), the TV remote can set the receiver's volume. This example maps
TV 0 → −60 dB and TV 20 → −30 dB (1.5 dB per press). It sends an **absolute**
value each time rather than up/down presses, so the two can't drift apart.

```yaml
alias: Sound System volume from TV remote
mode: restart
triggers:
  - trigger: state
    entity_id: media_player.tv
    attribute: volume_level
conditions:
  - "{{ trigger.to_state.attributes.volume_level is number }}"
  - condition: state
    entity_id: media_player.sound_system
    state: "on"
  - condition: state
    entity_id: media_player.sound_system
    attribute: source
    state: TV
actions:
  - variables:
      maxv: "{{ state_attr('media_player.sound_system', 'max_volume') | int(60) }}"
      # raw = 21 + 1.5 x TV level, clamped to 0..max_volume
      serial: >
        {{ ([0, [21 + 1.5 * ((trigger.to_state.attributes.volume_level | float) * 100) | round(0), maxv] | min] | max) | round(0) | int }}
  - action: media_player.volume_set
    target: { entity_id: media_player.sound_system }
    data: { volume_level: "{{ serial / maxv }}" }
```

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `Integration 'pioneer_serial' not found` | Home Assistant wasn't restarted after copying the folder, or the folder is in the wrong place (it must be `<config>/custom_components/pioneer_serial/`). |
| Entity is `unavailable` | Home Assistant can't reach the bridge. Check `docker ps`, `docker logs pioneer-bridge`, and that port 8102 is open in the bridge host's firewall. |
| Bridge exits with `cannot open /dev/ttyUSB0` | Wrong device path in `compose.yaml`, or the cable is unplugged. Check `ls -l /dev/serial/by-id/`. |
| Receiver turns itself back on after power-off | Something is sending it serial data while it's in standby, such as another program polling the port or a second integration. Only the bridge should open the port, and nothing should poll it while it's off. |
| Home Assistant shows on while the receiver is in standby | The receiver was turned off with **its own remote or front panel**. It doesn't report standby, so Home Assistant can't tell, and its next poll (≤ 30 s) will wake the receiver. Turn it off from Home Assistant instead. |
| Volume slider shows 100 % but the receiver is quieter than expected | Raise `max_volume`. Keep the cap below a level that could damage your speakers. |
| Each command and reply | `docker logs -f pioneer-bridge` shows each command sent (`amp<`) and each reply received (`amp>`). |

## Known limitations

- **Turning off from the receiver's own remote or panel isn't detected** (see above).
- Changes made on the receiver itself (knob, its remote) appear in Home
  Assistant on the next poll, at most 30 s later, while the receiver is on.
- The volume formula and input codes were verified on one VSX-9130 only.
- No Home Assistant UI setup (config flow) yet: configuration is YAML only.
- No HACS packaging yet: install by copying the folder.
- The bridge has no authentication or TLS.

## Protocol notes (as observed on the VSX-9130)

- 9600 baud, 8N1. Commands end with `CR`, replies end with `CR LF`.
- Queries: `?P` → `PWR0` (on); `PWR1`/`PWR2` mean standby in the Pioneer protocol but were never actually seen, because the receiver is silent in standby, `?V` → `VOL39`,
  `?M` → `MUT0` (muted) / `MUT1` (not muted), `?F` → `FN05`.
- Commands: `PO`, `PF`, `VU`, `VD`, `NNVL` or `NNNVL` (set volume),
  `MO` / `MF` (mute on/off), `NNFN` (select input).
- Unsupported (`E04`): `?RGB` (input names), `?FL` (front-panel display text).
- When the receiver wakes from standby, its first reply starts with one
  garbage byte (for example `\xffPWR0`).
- The receiver can drop commands sent back to back, so the bridge leaves 100 ms
  between commands.

## Credits

Made with AI: written by Claude (Anthropic) through Claude Code, together with
the repository owner, who supplied the hardware, testing and requirements.
Not affiliated with Pioneer, Onkyo or Home Assistant.

## License

[MIT](LICENSE): free to use, modify and redistribute, with no warranty.
