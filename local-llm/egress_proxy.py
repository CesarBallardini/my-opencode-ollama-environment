"""Local egress proxy for the local-llm profiles: open web allowed, model and tool vendors refused.

Listens on 127.0.0.1 only. Handles HTTPS through CONNECT and plain HTTP through absolute-URI
requests. A host is refused when it equals, or is a subdomain of, any entry in
egress-denylist.txt (one domain per line, '#' starts a comment); the file is re-read when it
changes, so editing it needs no restart. Every request is appended to egress.log as
'<time> <ALLOW|DENY> <method> <host>:<port>', which is the audit trail of what the profiles
reached. Standard library only.

Run:  uv run --no-project --python 3.14 python -I egress_proxy.py [--port 8899]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
DENYLIST = HERE / 'egress-denylist.txt'
LOG = HERE / 'egress.log'

_denied: frozenset[str] = frozenset()
_denylist_mtime = -1.0


def denied_domains() -> frozenset[str]:
    """The deny list, re-read whenever the file's modification time changes."""
    global _denied, _denylist_mtime
    mtime = DENYLIST.stat().st_mtime
    if mtime != _denylist_mtime:
        entries = (line.split('#', 1)[0].strip().lower().lstrip('.') for line in DENYLIST.read_text().splitlines())
        _denied = frozenset(entry for entry in entries if entry)
        _denylist_mtime = mtime
    return _denied


def is_denied(host: str) -> bool:
    """Whether ``host`` is a listed domain or one of its subdomains."""
    host = host.lower().rstrip('.')
    labels = host.split('.')
    return any('.'.join(labels[i:]) in denied_domains() for i in range(len(labels)))


def log(verdict: str, method: str, host: str, port: int) -> None:
    stamp = datetime.datetime.now().isoformat(timespec='seconds')
    with LOG.open('a', encoding='utf-8') as handle:
        handle.write(f'{stamp} {verdict} {method} {host}:{port}\n')


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(ConnectionError, asyncio.IncompleteReadError, OSError):
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    with contextlib.suppress(OSError):
        writer.close()


async def refuse(writer: asyncio.StreamWriter, host: str) -> None:
    body = f'egress proxy: {host} is on the local-llm deny list\n'.encode()
    writer.write(
        b'HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nConnection: close\r\n'
        + f'Content-Length: {len(body)}\r\n\r\n'.encode()
        + body
    )
    with contextlib.suppress(OSError):
        await writer.drain()
        writer.close()


async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    try:
        head = await client_reader.readuntil(b'\r\n\r\n')
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        client_writer.close()
        return
    request_line, *header_lines = head.decode('latin-1').split('\r\n')
    try:
        method, target, version = request_line.split(' ', 2)
    except ValueError:
        client_writer.close()
        return

    if method.upper() == 'CONNECT':
        host, _, port_text = target.rpartition(':')
        host, port = host.strip('[]'), int(port_text or 443)
    else:
        url = urlsplit(target)
        if not url.hostname:
            await refuse(client_writer, '(no host: not a proxy request)')
            return
        host, port = url.hostname, url.port or 80

    if is_denied(host):
        log('DENY', method, host, port)
        await refuse(client_writer, host)
        return
    log('ALLOW', method, host, port)

    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=20)
    except (OSError, asyncio.TimeoutError):
        client_writer.write(b'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
        with contextlib.suppress(OSError):
            await client_writer.drain()
            client_writer.close()
        return

    if method.upper() == 'CONNECT':
        client_writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        await client_writer.drain()
    else:
        url = urlsplit(target)
        path = (url.path or '/') + (f'?{url.query}' if url.query else '')
        kept = [line for line in header_lines if line and not line.lower().startswith(('proxy-connection:', 'proxy-authorization:'))]
        upstream_writer.write(('\r\n'.join([f'{method} {path} {version}', *kept]) + '\r\n\r\n').encode('latin-1'))
        await upstream_writer.drain()

    await asyncio.gather(pipe(client_reader, upstream_writer), pipe(upstream_reader, client_writer))


async def main(port: int) -> None:
    server = await asyncio.start_server(handle, '127.0.0.1', port)
    print(f'egress proxy on 127.0.0.1:{port}; deny list {DENYLIST}; log {LOG}', flush=True)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=8899)
    asyncio.run(main(parser.parse_args().port))
