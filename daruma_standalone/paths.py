"""Keep frozen resources readable and runtime data outside the bundled library."""
from pathlib import Path
import sys


def locations(module_file=__file__, *, frozen=None, executable=None):
    packaged = getattr(sys, 'frozen', False) if frozen is None else frozen
    module_root = Path(module_file).resolve().parent
    root = Path(executable or sys.executable).resolve().parent if packaged else module_root.parent
    runtime = root/'runtime' if packaged else module_root/'runtime'
    return root, runtime, module_root/'samples'


ROOT, RUNTIME, SAMPLES = locations()
