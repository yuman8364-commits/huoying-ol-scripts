from pathlib import Path
import os, runpy, sys
base=Path(__file__).resolve().parent/'client'
sys.path.insert(0,str(base))
os.chdir(base)
runpy.run_path(str(base/'hyol_desktop.py'),run_name='__main__')
