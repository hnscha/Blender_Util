# SPDX-License-Identifier: GPL-3.0-or-later
from . import icons, panels, quick_access, pose_panels


def register():
    icons.register()
    panels.register()
    quick_access.register()
    pose_panels.register()


def unregister():
    pose_panels.unregister()
    quick_access.unregister()
    panels.unregister()
    icons.unregister()
