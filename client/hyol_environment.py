"""Conservative native-thread defaults, applied before NumPy is imported."""
import os


def configure_numeric_runtime(environ=None):
    # Recognition is FFT/elementwise work, not a large dense BLAS workload.
    # Respect explicit deployment settings; only the previously implicit
    # OpenBLAS default is capped to avoid a large idle native thread pool.
    environ = os.environ if environ is None else environ
    environ.setdefault('OPENBLAS_NUM_THREADS', '2')
