# SPDX-License-Identifier: GPL-3.0-or-later
"""새 GUI Blender에서 실제 확장 활성화·아이콘 타이머 수명을 검사한다."""
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

sys.dont_write_bytecode = True
assert not bpy.app.background, 'Run in a separate factory GUI process'
source = Path(__file__).resolve().parents[1]
fixture = tempfile.TemporaryDirectory(prefix='anim_studio_extension_')
package = Path(fixture.name) / 'anim_studio'
package.mkdir()
for name in ('__init__.py', 'blender_manifest.toml'):
    shutil.copy2(source / name, package / name)
for name in ('core', 'ops', 'ui', 'icons'):
    shutil.copytree(source / name, package / name,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
repo = bpy.context.preferences.extensions.repos.new(
    name='Anim Studio Load Test', module='anim_studio_load_test',
    custom_directory=fixture.name)
module_name = 'bl_ext.' + repo.module + '.anim_studio'
before_objects = [(obj.as_pointer(), obj.name) for obj in bpy.data.objects]
before_images = {image.as_pointer() for image in bpy.data.images}


def fail(error):
    raise error


def enable():
    module = addon_utils.enable(module_name, default_set=True, handle_error=fail)
    assert module is not None and module.__addon_enabled__
    icons = module.ui.icons
    assert len(icons._previews) == 66
    assert bpy.app.timers.is_registered(icons._load_pixels)
    return module


# 등록 직후 비활성화해도 대기 중인 타이머가 남지 않아야 한다.
module = enable()
icons = module.ui.icons
addon_utils.disable(module_name, default_set=True, handle_error=fail)
assert icons._previews is None
assert not bpy.app.timers.is_registered(icons._load_pixels)
module = enable()


def verify():
    try:
        icons = module.ui.icons
        assert not bpy.app.timers.is_registered(icons._load_pixels)
        for name, preview in icons._previews.items():
            size = 64 if name.endswith('@2x') else 32
            assert tuple(preview.image_size) == (size, size), name
            assert len(preview.image_pixels) == size * size, name
            assert preview.icon_id > 0, name
        assert before_images == {image.as_pointer() for image in bpy.data.images}
        assert before_objects == [(obj.as_pointer(), obj.name) for obj in bpy.data.objects]
        assert [item.identifier for item in bpy.types.WindowManager.bl_rna.properties[
            'anim_studio_tab'].enum_items] == ['POSE', 'KEY', 'SPACE', 'HOTKEY']
        area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
        with bpy.context.temp_override(area=area):
            bpy.ops.anim_studio.focus_panel()
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        addon_utils.disable(module_name, default_set=True, handle_error=fail)
        assert icons._previews is None
        assert not bpy.app.timers.is_registered(icons._load_pixels)
        print('EXTENSION_LOAD_OK: 66 icons, four tabs, enable/disable/enable, no timer/image/object leak', flush=True)
    except Exception:
        traceback.print_exc()
        print('EXTENSION_LOAD_FAILED', flush=True)
    finally:
        bpy.context.preferences.extensions.repos.remove(repo)
        fixture.cleanup()
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(verify, first_interval=1.0)
