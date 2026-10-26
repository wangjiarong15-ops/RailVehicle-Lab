"""Public deployment entry point; never enables private data or paid AI."""

import os

os.environ["RAIL_PUBLIC_DEMO"] = "true"

from app import main

if __name__ == "__main__":
    main()
