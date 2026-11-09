from pathlib import Path
import os, sys, unittest
base = Path(__file__).resolve().parent
sys.path.insert(0, str(base / 'client'))
os.chdir(base / 'client')
suite = unittest.defaultTestLoader.discover(str(base / 'tests/portable'))
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
