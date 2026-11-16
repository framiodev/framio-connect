import sys

from framio.ui import App

if __name__ == "__main__":
    App(autostart="--autostart" in sys.argv).run()
