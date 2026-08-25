"""Assemble the public Karat Board - the static half of the app.

The board a visitor sees is the same index.html/app.js/style.css the local app
serves. The only difference is where the numbers come from: locally the page
asks the Python for /api/state, and on a static host it reads the rates.json
that a scheduled build left next to it.

So "hosting" this app is just: run the sweep on a timer somewhere, drop the
result beside the page, and let a CDN serve the lot. No server, no bill.

    python build_site.py [outdir]        # default: ./site

Then publish outdir/ anywhere that serves files. GitHub Pages, an S3 bucket, a
folder on any web host - they all work, because none of them have to run
anything.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

# Everything the page needs. Nothing else from static/ is public.
#
# The YourCardJourney logo is deliberately absent: the site repo ignores *.png,
# the live site already 404s it, and it is 1.4 MB. The credit line is text and a
# link instead, which is what the published page actually shows.
ASSETS = ("index.html", "app.js", "style.css", "favicon.svg")


def build(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name in ASSETS:
        src = os.path.join(STATIC, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(out_dir, name))
            print("  copied", name)
        else:
            print("  skipped (missing)", name)

    # The data. app.py --snapshot does the eight-merchant sweep and writes
    # rates.json; its exit code is non-zero if nothing at all could be read, so
    # a broken build fails loudly instead of publishing an empty board.
    print("sweeping merchants…")
    code = subprocess.call([sys.executable, os.path.join(HERE, "app.py"),
                            "--snapshot", out_dir])
    if code:
        print("sweep failed - not publishing an empty board")
        return code

    print("site ready in", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(build(os.path.abspath(sys.argv[1] if len(sys.argv) > 1
                                   else os.path.join(HERE, "site"))))
