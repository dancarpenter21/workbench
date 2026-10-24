"""Process-wide cooperation between the repository's PCA9685 writers."""

import fcntl
from pathlib import Path


class DeviceLock:
    def __init__(self, bus, address):
        path = Path(f"/tmp/workbench-pca9685-{bus}-{address:02x}.lock")
        self.handle = path.open("a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError("PCA9685 is occupied by another hardware writer") from exc

    def close(self):
        self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
