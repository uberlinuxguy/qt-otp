"""Entry script for the PyInstaller build.

PyInstaller needs a real script, and `otpvault/__main__.py` cannot be run as
one (its relative imports require the package context), so this hands straight
over to the package's main().
"""

import sys

from otpvault.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
