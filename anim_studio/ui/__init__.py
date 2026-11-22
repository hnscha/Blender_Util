# SPDX-License-Identifier: GPL-3.0-or-later
from . import panels


def register():
    panels.register()


def unregister():
    panels.unregister()
