# SPDX-License-Identifier: GPL-3.0-or-later
from . import hotkey_ops, pose_ops, space_ops, tween_ops, picker_ops, library_ops


def register():
    pose_ops.register()
    space_ops.register()
    tween_ops.register()
    picker_ops.register()
    library_ops.register()
    hotkey_ops.register()


def unregister():
    hotkey_ops.unregister()
    library_ops.unregister()
    picker_ops.unregister()
    tween_ops.unregister()
    space_ops.unregister()
    pose_ops.unregister()
