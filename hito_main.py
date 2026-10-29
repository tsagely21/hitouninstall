"""Entry script for running from source (python hito_main.py) and for the Nuitka build."""
from hito.app import main

if __name__ == "__main__":
    raise SystemExit(main())
