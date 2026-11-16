"""Applies the text input protocol (M/B/W/H/K/R) to the local machine via SendInput."""
from .winapi import BTN_FLAGS, XBUTTON_DOWN, XBUTTON_UP, key_input, mouse_input


class Injector:
    def __init__(self):
        self.keys, self.btns = set(), set()

    def handle(self, p):
        """p = list of words. Returns True if it was an input command."""
        if not p:
            return False
        t = p[0]
        try:
            if t == "M":
                mouse_input(0x0001 | 0x8000, int(p[1]), int(p[2]))
            elif t == "B":
                b, d = int(p[1]), int(p[2])
                if b in BTN_FLAGS:
                    mouse_input(BTN_FLAGS[b][0 if d else 1])
                elif b in (3, 4):
                    mouse_input(XBUTTON_DOWN if d else XBUTTON_UP, data=b - 2)
                else:
                    return True
                (self.btns.add if d else self.btns.discard)(b)
            elif t == "W":
                mouse_input(0x0800, data=int(p[1]))
            elif t == "H":
                mouse_input(0x1000, data=int(p[1]))
            elif t == "K":
                vk, sc, ext, d = int(p[1]), int(p[2]), int(p[3]), int(p[4])
                if not (1 <= vk <= 254):
                    return True
                key_input(vk, sc, ext, d)
                (self.keys.add if d else self.keys.discard)((vk, sc, ext))
            elif t == "R":
                self.release_all()
            else:
                return False
        except (IndexError, ValueError):
            pass
        return True

    def release_all(self):
        for vk, sc, ext in list(self.keys):
            key_input(vk, sc, ext, 0)
        for b in list(self.btns):
            if b in BTN_FLAGS:
                mouse_input(BTN_FLAGS[b][1])
            else:
                mouse_input(XBUTTON_UP, data=b - 2)
        self.keys.clear()
        self.btns.clear()
