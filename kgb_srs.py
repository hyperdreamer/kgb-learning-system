#!/usr/bin/env python3
"""KGB 5-Box SRS System — launcher script.

For development and backwards compatibility. The main code now lives in the
kgb_srs/ package.  To run the application:

    python kgb_srs.py          # this file (launcher)
    python main.py             # entry point
"""

from main import main


if __name__ == "__main__":
    raise SystemExit(main())
