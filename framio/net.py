"""Shared networking helpers: HMAC challenge/response handshake, keepalive."""
import hashlib
import hmac
import secrets
import socket


def mac(key, msg):
    return hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def keepalive(sock):
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    try:
        sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 3000))
    except Exception:
        pass


class AuthError(RuntimeError):
    pass


def client_handshake(host, port, key, fields, timeout=10, reply_timeout=None):
    """Connect + mutual HMAC auth. `fields` are the space-free words appended to HELLO.
    Returns (sock, file). Raises AuthError on a rejected/unauthentic peer."""
    s = socket.create_connection((host, port), timeout=5)
    try:
        keepalive(s)
        s.settimeout(timeout)
        f = s.makefile("rb")
        p = f.readline(512).decode(errors="replace").split()
        if len(p) != 2 or p[0] != "CHAL":
            raise AuthError("invalid server")
        nonce, cn = p[1], secrets.token_hex(16)
        s.sendall(f"HELLO {mac(key, nonce)} {cn} {' '.join(fields)}\n".encode())
        if reply_timeout:
            s.settimeout(reply_timeout)
        r = f.readline(512).decode(errors="replace").split()
        s.settimeout(timeout)
        if not r or r[0] != "OK":
            raise AuthError("rejected" if not r or r[0] == "NO" else r[0].lower())
        if len(r) < 2 or not hmac.compare_digest(r[1], mac(key, "S" + nonce + cn)):
            raise AuthError("server not authentic")
        return s, f
    except Exception:
        s.close()
        raise


def server_challenge(s):
    nonce = secrets.token_hex(16)
    s.sendall(f"CHAL {nonce}\n".encode())
    return nonce


def server_verify(key, nonce, mac_hex):
    return hmac.compare_digest(mac_hex, mac(key, nonce))


def server_proof(key, nonce, cn):
    return mac(key, "S" + nonce + cn)


def local_ips():
    ips = set()
    try:
        for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if not a[4][0].startswith(("127.", "169.254.")):
                ips.add(a[4][0])
    except Exception:
        pass
    return sorted(ips)
