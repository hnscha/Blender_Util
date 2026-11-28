# SPDX-License-Identifier: GPL-3.0-or-later
from . import icons, panels, quick_access, pose_panels, library_panels


def register():
    icons.register()
    panels.register()
    quick_access.register()
    library_panels.register()  # 같은 탭에서 피커·포즈 라이브러리를 먼저(위에) 보인다.
    pose_panels.register()


def unregister():
    pose_panels.unregister()
    library_panels.unregister()
    quick_access.unregister()
    panels.unregister()
    icons.unregister()
