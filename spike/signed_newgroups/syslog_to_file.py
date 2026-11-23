"""
Write what INN sends to syslog into /var/log/news/syslog.

The image runs no syslog daemon, so the messages of innd, controlchan and pgpverify
are lost. The spike tests start this in each node to see why a control article was
refused.
"""

import socket
from pathlib import Path

SOCKET = Path("/dev/log")
LOG = Path("/var/log/news/syslog")

SOCKET.unlink(missing_ok=True)
server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
server.bind(str(SOCKET))
SOCKET.chmod(0o666)  # INN logs as user news, while this runs as root
with LOG.open("a", buffering=1) as log:
    while True:
        log.write(server.recv(65535).decode(errors="replace") + "\n")
