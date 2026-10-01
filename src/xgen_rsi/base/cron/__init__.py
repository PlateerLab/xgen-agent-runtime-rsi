"""Cron subsystem — scheduled background tasks.

Public surface:

* :class:`CronJob`, :class:`CronJobStatus` — record types.
* :class:`CronJobStore` — ABC.
* :class:`InMemoryCronJobStore`, :class:`FileBackedCronJobStore` —
  reference impls.
* :class:`CronRunner` — asyncio daemon (PR-A.4.3).
"""

from xgen_rsi.base.cron.runner import CronRunner
from xgen_rsi.base.cron.store_abc import CronJobStore
from xgen_rsi.base.cron.store_impl.file_backed import FileBackedCronJobStore
from xgen_rsi.base.cron.store_impl.in_memory import InMemoryCronJobStore
from xgen_rsi.base.cron.types import CronJob, CronJobStatus

__all__ = [
    "CronJob",
    "CronJobStatus",
    "CronJobStore",
    "CronRunner",
    "FileBackedCronJobStore",
    "InMemoryCronJobStore",
]
