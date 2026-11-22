# SPDX-License-Identifier: GPL-3.0-or-later
from . import hotkey_ops


def register():
    hotkey_ops.register()


def unregister():
    hotkey_ops.unregister()
