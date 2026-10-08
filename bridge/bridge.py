"""Serial <-> TCP bridge for the Pioneer VSX RS-232 port.

Sole owner of the serial device. Every line the amp sends is broadcast to
all TCP clients; every line a client sends is written to the amp. `#PING` is
answered by the bridge itself and never reaches the amp. Exits on
serial errors so Docker restarts it (and re-resolves the device path).
"""
import asyncio, logging, os, sys
import serial

DEVICE = os.environ.get("SERIAL_DEVICE", "/dev/ttyUSB0")
BAUD = int(os.environ.get("SERIAL_BAUD", "9600"))
PORT = int(os.environ.get("LISTEN_PORT", "8102"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pioneer-bridge")
clients: set[asyncio.StreamWriter] = set()


def fatal(msg):
    log.error(msg)
    os._exit(1)


async def serial_reader(ser):
    loop = asyncio.get_running_loop()
    buf = b""
    while True:
        try:
            chunk = await loop.run_in_executor(None, ser.read, 256)
        except serial.SerialException as e:
            fatal(f"serial read failed: {e}")
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            log.info("amp> %s", line.decode(errors="replace"))
            for w in list(clients):
                try:
                    w.write(line + b"\r\n")
                except Exception:
                    clients.discard(w)


async def handle_client(ser, lock, reader, writer):
    peer = writer.get_extra_info("peername")
    log.info("client connected %s", peer)
    clients.add(writer)
    try:
        buf = b""
        while chunk := await reader.read(256):
            # Commands end in CR, LF or CRLF; drop telnet IAC negotiation bytes.
            *lines, buf = (buf + chunk).replace(b"\r", b"\n").split(b"\n")
            for cmd in lines:
                cmd = bytes(b for b in cmd if 32 <= b < 127).strip()
                if not cmd:
                    continue
                if cmd == b"#PING":
                    # Liveness check answered locally: any serial byte wakes the amp from standby.
                    writer.write(b"#PONG\r\n")
                    continue
                log.info("amp< %s (%s)", cmd.decode(), peer[0])
                async with lock:
                    try:
                        ser.write(cmd + b"\r")
                    except serial.SerialException as e:
                        fatal(f"serial write failed: {e}")
                    await asyncio.sleep(0.1)  # amp drops commands sent back-to-back
    except ConnectionError:
        pass
    finally:
        clients.discard(writer)
        writer.close()
        log.info("client disconnected %s", peer)


async def main():
    try:
        ser = serial.Serial(DEVICE, BAUD, timeout=0.2)
    except serial.SerialException as e:
        fatal(f"cannot open {DEVICE}: {e}")
    lock = asyncio.Lock()
    server = await asyncio.start_server(
        lambda r, w: handle_client(ser, lock, r, w), "0.0.0.0", PORT)
    log.info("bridging %s @%d <-> tcp/%d", DEVICE, BAUD, PORT)
    async with server:
        await asyncio.gather(server.serve_forever(), serial_reader(ser))

asyncio.run(main())
