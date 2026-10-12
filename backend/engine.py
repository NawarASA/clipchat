"""Entry point for the bundled engine (clipchat-engine.exe). Development runs `python -m clipchat`."""

import multiprocessing

from clipchat.server import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
