# Pioneer VSX RS-232 → Home Assistant

Exposes an older Pioneer VSX receiver (tested: VSX-9130) as a Home Assistant
`media_player` through its RS-232 port.

```
amp RS-232 ── USB/serial ── bridge/ (Docker, serial↔TCP :8102) ── custom_components/pioneer_serial (HA)
```

## Why not the built-in `pioneer` integration

This amp speaks an older dialect: 2-digit volume (`VOL39`, dB = value − 81), no
`?RGB` input names. The built-in integration hard-codes a 0–185 volume scale, so
50 % on its slider sends `093VL` — far louder than intended.

Also: **in standby the amp is silent and any serial byte wakes it up**, and it
never reports standby after `PF`. The integration therefore marks the amp off
optimistically, only polls it while on, and checks the link with `#PING`, which
the bridge answers without touching the serial port.

## Bridge

```sh
cd bridge
# edit the device path in compose.yaml (use /dev/serial/by-id/...)
docker compose up -d --build
```

Sole owner of the serial port; broadcasts every amp line to all TCP clients.
Exits on serial errors so Docker restarts it. No authentication: keep port 8102
on a trusted LAN.

## Home Assistant

Copy `custom_components/pioneer_serial` into `<config>/custom_components/`, then:

```yaml
media_player:
  - platform: pioneer_serial
    host: 192.168.5.113      # bridge host
    port: 8102
    name: Sound System
    max_volume: 60           # raw value shown as 100 %; never exceeded (60 = -21 dB)
    sources:                 # name: Pioneer input code
      TV: "05"
      DVD: "04"
      HDMI 1: "19"
```

Restart Home Assistant. State updates are pushed by the amp; the entity exposes
`raw_volume` and `max_volume` attributes.
