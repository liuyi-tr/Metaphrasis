"""Where the program under test is: a folder named on the command line, or else the folder this tests folder is in.

Every suite starts with `from _app import APP`, which also puts that folder first on the import path."""
import os
import sys

APP = next((os.path.abspath(a) for a in sys.argv[1:] if not a.startswith("--") and os.path.isdir(a)),
           os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, APP)
