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


def register():
    global _previews
    previews = bpy.utils.previews.new()
    try:
        for path in sorted(_directory.glob('*.png')):
            # PNG 썸네일 작업 큐는 Windows 비 ASCII 경로에서 캐시 쓰기에
            # 실패할 수 있다. 등록 중 픽셀을 읽어 미리보기를 완성한다.
            image = bpy.data.images.load(str(path), check_existing=False)
            try:
                preview = previews.new(path.stem)
                preview.image_size = preview.icon_size = tuple(image.size)
                pixels = image.pixels[:]
                preview.image_pixels_float = pixels
                preview.icon_pixels_float = pixels
            finally:
                bpy.data.images.remove(image)
    except BaseException:
        bpy.utils.previews.remove(previews)
        raise
    _previews = previews


def unregister():
    global _previews
    bpy.utils.previews.remove(_previews)
    _previews = None
