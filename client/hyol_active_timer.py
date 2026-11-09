"""Monotonic active segments; an idle UI never advances a run's elapsed time."""
import json
import time
from pathlib import Path
from datetime import datetime


class ActiveTimer:
    def __init__(self, folder, clock=time.monotonic):
        self.folder, self.clock = Path(folder), clock
        self.identifier, self.accumulated, self.since, self.finished = '', 0.0, None, ''

    def begin(self, identifier):
        self.stop()
        self.identifier = str(identifier)
        self.accumulated, self.finished = self._previous(identifier), ''
        self.since = self.clock()

    def _previous(self, identifier):
        try:
            row = json.loads((self.folder / (str(identifier) + '.json')).read_text('utf-8'))
            return max(0.0, float(row.get('elapsedSeconds', 0)))
        except (OSError, ValueError, TypeError):
            return 0.0

    def rebind(self, identifier):
        if identifier and identifier != self.identifier:
            segment = self.elapsed()
            self.identifier = str(identifier)
            self.accumulated = self._previous(identifier) + segment
            self.since = self.clock() if self.since is not None else None

    def elapsed(self):
        return self.accumulated + (max(0.0, self.clock() - self.since) if self.since is not None else 0.0)

    def stop(self):
        if self.since is None:
            return
        self.accumulated = self.elapsed()
        self.since = None
        self.finished = datetime.now().astimezone().isoformat(timespec='seconds')
        if self.identifier:
            from hyol_scheduler import atomic_json
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                atomic_json(self.folder / (self.identifier + '.json'), self.snapshot())
            except OSError:
                pass  # Timing must never prevent stop/receipt cleanup.

    def snapshot(self):
        return dict(elapsedSeconds=round(self.elapsed(), 3), timerRunning=self.since is not None,
                    finishedAt=self.finished)
