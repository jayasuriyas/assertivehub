"""v6 "Connected Operations": branches, notifications, meetings, task time tracking.

Every action is reached through the existing /api/?a=<module>.<action> endpoint (see base.dispatch).
Handlers register themselves with @action in their module; importing this package loads them all.
"""
from . import base  # noqa: F401


def load_all():
    from . import branches, worktime, meetings, notify, calsync, reports, files, system  # noqa: F401
