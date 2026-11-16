"""Framio visual identity: colour palettes and the logo (drawn with Pillow, no image files)."""
from PIL import Image, ImageDraw

DARK = {
    "bg": "#0A1020", "side": "#0E1730", "card": "#111C36", "card2": "#18264A", "input": "#0C1630",
    "border": "#243459", "text": "#EAF0FB", "muted": "#8DA0C4",
    "accent": "#1FD6C6", "accent_hover": "#4AE6D8", "accent_fg": "#04201E",
    "danger": "#FF5C7A", "ok": "#3DDC97", "warn": "#FFB84D",
}
LIGHT = {
    "bg": "#EEF3FA", "side": "#FFFFFF", "card": "#FFFFFF", "card2": "#EAF0F9", "input": "#F6F9FD",
    "border": "#D6DFEE", "text": "#0E1A33", "muted": "#5B6C8C",
    "accent": "#0CB3A5", "accent_hover": "#0A9A8E", "accent_fg": "#FFFFFF",
    "danger": "#E5365A", "ok": "#12A66A", "warn": "#D98A12",
}


def system_prefers_light():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1
    except OSError:
        return False


def palette(theme):
    if theme == "auto":
        theme = "light" if system_prefers_light() else "dark"
    return (LIGHT if theme == "light" else DARK), theme


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def logo_image(size=256):
    """Rounded square, turquoise->blue gradient, a bold 'F' whose top bar ends in a connection node."""
    s = 4
    n = size * s
    grad = Image.new("RGB", (n, n))
    px = grad.load()
    c1, c2 = (31, 214, 198), (22, 112, 224)
    for y in range(n):
        for x in range(n):
            px[x, y] = _lerp(c1, c2, (x + y) / (2 * n))
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, n - 1, n - 1], radius=int(n * 0.24), fill=255)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    w = (255, 255, 255, 255)
    u = n / 100.0
    d.rounded_rectangle([30 * u, 24 * u, 42 * u, 76 * u], radius=5 * u, fill=w)        # stem
    d.rounded_rectangle([30 * u, 24 * u, 64 * u, 36 * u], radius=5 * u, fill=w)        # top bar
    d.rounded_rectangle([30 * u, 45 * u, 56 * u, 56 * u], radius=5 * u, fill=w)        # middle bar
    d.ellipse([62 * u, 22 * u, 78 * u, 38 * u], fill=(10, 16, 32, 255))                # node ring
    d.ellipse([65 * u, 25 * u, 75 * u, 35 * u], fill=w)
    return img.resize((size, size), Image.LANCZOS)
