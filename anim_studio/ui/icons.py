# SPDX-License-Identifier: GPL-3.0-or-later
"""애드온 등록 수명에 맞춰 전용 PNG 미리보기를 관리한다."""
from pathlib import Path

import bpy
import bpy.utils.previews

_previews = None
_directory = Path(__file__).resolve().parents[1] / 'icons'


def icon_id(name, context=None):
    context = bpy.context if context is None else context
    system = context.preferences.system
    suffix = '@2x' if max(system.ui_scale, system.pixel_size) >= 1.5 else ''
    return _previews[name + suffix].icon_id


def _load_pixels():
    if _previews is None:
        return None
    for path in sorted(_directory.glob('*.png')):
        # PNG 썸네일 작업 큐는 Windows 비 ASCII 경로에서 캐시 쓰기에
        # 실패할 수 있어 이미지 픽셀을 직접 복사한다.
        image = bpy.data.images.load(str(path), check_existing=False)
        try:
            preview = _previews[path.stem]
            preview.image_size = preview.icon_size = tuple(image.size)
            pixels = image.pixels[:]
            preview.image_pixels_float = pixels
            preview.icon_pixels_float = pixels
        finally:
            bpy.data.images.remove(image)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
    return None


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    try:
        for path in sorted(_directory.glob('*.png')):
            _previews.new(path.stem)
        # 실제 애드온 활성화는 _RestrictData 아래에서 register()를 호출한다.
        # 아이콘 ID는 먼저 만들고 이미지 접근은 등록이 끝난 다음 한 번 실행한다.
        if hasattr(bpy.data, 'images'):
            _load_pixels()
        else:
            bpy.app.timers.register(_load_pixels, first_interval=0.0)
    except BaseException:
        unregister()
        raise


def unregister():
    global _previews
    if bpy.app.timers.is_registered(_load_pixels):
        bpy.app.timers.unregister(_load_pixels)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
    _previews = None
