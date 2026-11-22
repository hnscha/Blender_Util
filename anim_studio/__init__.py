# SPDX-License-Identifier: GPL-3.0-or-later
from . import core, ops, ui


def register():
    core.register()
    ops.register()
    ui.register()


def unregister():
    ui.unregister()
    ops.unregister()
    core.unregister()
