"""Isolated Daruma experiment. Never registers a task with the main client."""

# This package imports NumPy in several submodules (engine/runtime/vision).
# Running before those imports keeps the OpenBLAS default consistent with the
# unified client; an explicit OPENBLAS_NUM_THREADS is always respected. A
# stripped standalone bundle without the shared modules keeps its old default.
try:
    from hyol_environment import configure_numeric_runtime
    configure_numeric_runtime()
except ImportError:
    pass

VERSION = '2026.10.18.2'
