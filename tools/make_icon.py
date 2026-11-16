import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from framio.brand import logo_image  # noqa: E402

os.makedirs("assets", exist_ok=True)
img = logo_image(256)
img.save("assets/framio.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save("assets/framio.png")
print("icon written")
