#!/usr/bin/env python3
import argparse
import asyncio
import logging
import signal
from contextlib import suppress


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, direction: str) -> None:
    try:
        while True:
            data = await reader.read(1024 * 64)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError) as exc:
        logging.info("%s closed: %s", direction, exc)
    except Exception:
        logging.exception("%s failed", direction)
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()


async def handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_host: str,
    upstream_port: int,
    connect_timeout: float,
) -> None:
    client = client_writer.get_extra_info("peername")
    logging.info("accepted client=%s upstream=%s:%s", client, upstream_host, upstream_port)
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(upstream_host, upstream_port),
            timeout=connect_timeout,
        )
    except Exception as exc:
        logging.error("upstream connect failed client=%s upstream=%s:%s error=%s", client, upstream_host, upstream_port, exc)
        client_writer.close()
        with suppress(Exception):
            await client_writer.wait_closed()
        return

    await asyncio.gather(
        pipe(client_reader, upstream_writer, f"client-to-upstream {client}"),
        pipe(upstream_reader, client_writer, f"upstream-to-client {client}"),
    )
    logging.info("closed client=%s", client)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Small TCP forwarding proxy for MiniFi JDBC sockets")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", required=True, type=int)
    parser.add_argument("--upstream-host", required=True)
    parser.add_argument("--upstream-port", required=True, type=int)
    parser.add_argument("--connect-timeout", default=15.0, type=float)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = await asyncio.start_server(
        lambda reader, writer: handle_client(reader, writer, args.upstream_host, args.upstream_port, args.connect_timeout),
        host=args.listen_host,
        port=args.listen_port,
        reuse_address=True,
    )
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    logging.info("listening on %s forwarding to %s:%s", sockets, args.upstream_host, args.upstream_port)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    async with server:
        await stop_event.wait()
    logging.info("proxy shutdown requested")


if __name__ == "__main__":
    asyncio.run(main())
