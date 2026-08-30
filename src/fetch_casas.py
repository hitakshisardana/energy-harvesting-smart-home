"""
Pull a few individual testbed CSVs out of the CASAS 'free living' labeled dataset
on Zenodo (record 15708568, labeled_data.zip, 236 MB) WITHOUT downloading the
whole archive.

Zenodo serves the file with HTTP range support, so we mount the remote zip as a
seekable file object and let the stdlib `zipfile` read just the members we want
(a few MB instead of 236 MB).

Each file is a CSV with no header:
    date, time(HH:MM:SS.ffffff), zone/sensor, state(ON|OFF|value), [activity label]
e.g.  2012-07-20,10:38:54.512364,OutsideDoor,ON,Step_Out="begin"

  hh1xx  = single/1-2 resident apartments, 2012+, motion + OutsideDoor + light
  mn5x   = Milan, single resident + pet, 2019, motion + door, Sleep labels
Full testbed list is printed when you run this.

Usage:
    python src/fetch_casas.py                 # curated starter set
    python src/fetch_casas.py hh101 mn50      # specific testbeds
    python src/fetch_casas.py --list          # just list what's in the archive
"""
import io
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ZIP_URL = "https://zenodo.org/api/records/15708568/files/labeled_data.zip/content"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"


class HttpRangeFile(io.RawIOBase):
    """Minimal seekable read-only file backed by HTTP range requests."""

    def __init__(self, url, chunk_hint=1 << 16):
        self.url = url
        self._pos = 0
        self._chunk_hint = chunk_hint
        # probe with a 1-byte range GET; HEAD on Zenodo omits Accept-Ranges
        req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            if r.status != 206:
                raise RuntimeError(f"range request not honoured (HTTP {r.status})")
            cr = r.headers.get("Content-Range", "")
            self._size = int(cr.rsplit("/", 1)[1])
        self.bytes_fetched = 0
        self.requests = 0

    # --- io plumbing ---
    def seekable(self):
        return True

    def readable(self):
        return True

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        elif whence == io.SEEK_END:
            self._pos = self._size + offset
        return self._pos

    def tell(self):
        return self._pos

    def read(self, size=-1):
        if size is None or size < 0:
            size = self._size - self._pos
        if size <= 0 or self._pos >= self._size:
            return b""
        end = min(self._pos + size, self._size) - 1
        req = urllib.request.Request(
            self.url, headers={"Range": f"bytes={self._pos}-{end}"}
        )
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 4:
                    raise
                time.sleep(2 * (attempt + 1))
                print(f"  retry {attempt+1} after {exc}")
        self._pos += len(data)
        self.bytes_fetched += len(data)
        self.requests += 1
        return data


STARTER_SET = ["hh101", "hh104", "mn50", "mn51"]


def stem(name):
    return name.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()


def main():
    args = [a.lower() for a in sys.argv[1:]]
    list_only = "--list" in args
    wanted = [a for a in args if not a.startswith("-")] or STARTER_SET
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Mounting remote zip: {ZIP_URL}")
    rf = HttpRangeFile(ZIP_URL)
    print(f"  archive size: {rf._size/1e6:.1f} MB")

    zf = zipfile.ZipFile(rf)
    names = [n for n in zf.namelist() if not n.endswith("/")]
    print(f"  {len(names)} files in archive")

    stems = sorted({stem(n) for n in names})
    print(f"  testbeds present:\n    {', '.join(stems)}")
    if list_only:
        return

    selected = [n for n in names if stem(n) in wanted]
    if not selected:
        sys.exit(f"none of {wanted} found; run with --list to see options")

    total = sum(zf.getinfo(n).file_size for n in selected)
    print(f"\nExtracting {len(selected)} files for {wanted} "
          f"(~{total/1e6:.1f} MB uncompressed) -> {OUT_DIR}")
    for n in selected:
        info = zf.getinfo(n)
        dest = OUT_DIR / Path(n).name
        with zf.open(info) as src, open(dest, "wb") as out:
            out.write(src.read())
        print(f"  {dest.name}  ({info.file_size/1e6:.2f} MB)")

    print(f"\nDone. Fetched {rf.bytes_fetched/1e6:.1f} MB over "
          f"{rf.requests} range requests (vs {rf._size/1e6:.0f} MB full download).")


if __name__ == "__main__":
    main()
