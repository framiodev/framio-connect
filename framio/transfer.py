"""File transfer stream: JSON header line + raw bytes per file, terminated by {"end":1}."""
import json
import os
import re

CHUNK = 1 << 20
_BAD = re.compile(r'[<>:"|?*\\\x00-\x1f]')


def downloads_dir():
    d = os.path.join(os.path.expanduser("~"), "Downloads", "Framio")
    os.makedirs(d, exist_ok=True)
    return d


def iter_files(paths):
    for p in paths:
        base = os.path.basename(p.rstrip("\\/")) or "file"
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for fn in files:
                    ap = os.path.join(root, fn)
                    rel = os.path.join(base, os.path.relpath(ap, p))
                    yield ap, rel.replace("\\", "/")
        elif os.path.isfile(p):
            yield p, base


def total_size(paths):
    t = 0
    for ap, _ in iter_files(paths):
        try:
            t += os.path.getsize(ap)
        except OSError:
            pass
    return t


def send_files(sock, f, paths, progress=None):
    """Stream files. `f` is the buffered reader of the same socket (to read the DONE ack)."""
    sent = 0
    for ap, rel in iter_files(paths):
        try:
            fh = open(ap, "rb")
            size = os.fstat(fh.fileno()).st_size
        except OSError:
            continue
        with fh:
            sock.sendall((json.dumps({"rel": rel, "size": size}) + "\n").encode())
            left = size
            while left > 0:
                chunk = fh.read(min(CHUNK, left))
                if not chunk:
                    raise IOError("file changed during transfer: " + ap)
                sock.sendall(chunk)
                left -= len(chunk)
                sent += len(chunk)
                if progress:
                    progress(sent)
    sock.sendall(b'{"end":1}\n')
    f.readline(64)  # DONE


def _safe(name):
    name = _BAD.sub("_", name).strip(" .")
    return name or "_"


def _unique(dest, name):
    if not os.path.exists(os.path.join(dest, name)):
        return name
    stem, ext = os.path.splitext(name)
    i = 1
    while os.path.exists(os.path.join(dest, f"{stem} ({i}){ext}")):
        i += 1
    return f"{stem} ({i}){ext}"


def recv_files(f, sock, dest, progress=None):
    """Receive a stream into `dest`. Returns the list of saved top-level paths."""
    os.makedirs(dest, exist_ok=True)
    top, saved, got = {}, [], 0
    while True:
        line = f.readline(65536)
        if not line:
            raise EOFError("transfer interrupted")
        h = json.loads(line)
        if h.get("end"):
            break
        size = int(h["size"])
        parts = [_safe(x) for x in str(h["rel"]).split("/") if x not in ("", ".", "..")]
        out = None
        if parts:
            if parts[0] not in top:
                top[parts[0]] = _unique(dest, parts[0])
                saved.append(os.path.join(dest, top[parts[0]]))
            parts[0] = top[parts[0]]
            path = os.path.join(dest, *parts)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            out = open(path, "wb")
        try:
            left = size
            while left > 0:
                data = f.read(min(CHUNK, left))
                if not data:
                    raise EOFError("transfer interrupted")
                if out:
                    out.write(data)
                left -= len(data)
                got += len(data)
                if progress:
                    progress(got)
        finally:
            if out:
                out.close()
    sock.sendall(b"DONE\n")
    return saved
