from tqdm import tqdm
import os
import sys
import json
import datetime
from contextlib import contextmanager

@contextmanager
def progressbar(**kwargs):
  fd = kwargs.get('file', sys.stderr)

  if fd.isatty():
    pbar = tqdm(**kwargs)
  else:
    pbar = progressbar_file(
      kwargs.get('desc', 'Something'),
      kwargs.get('total', None),
      kwargs.get('unit', 'it'),
      fd,
    )
  yield pbar
  pbar.close()

class progressbar_file:
  def __init__(self, desc, total, unit, fd):
    self._desc = desc
    self._total  = total if total is not None else -1
    self._unit = unit
    self._fd = fd

    self._count = 0
    # Print at least every 60 seconds.
    self._update_min = 60

    self._started_at = datetime.datetime.now()
    self._last_printed = self._started_at
    self._print()

  def update(self):
    self._count += 1
    if self._total > -1:
      assert self._count <= self._total

    now = datetime.datetime.now()
    if (now - self._last_printed).total_seconds() >= self._update_min:
      self._last_printed = now
      self._print()

  def _print(self):
    print(json.dumps({
      'desc': self._desc,
      'count': self._count,
      'total': self._total,
      'unit': self._unit,
      'started_at': str(self._started_at),
      'timestamp': str(self._last_printed),
    }), file=self._fd)

  def close(self):
    pass
