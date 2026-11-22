# SPDX-License-Identifier: GPL-3.0-or-later
"""별도 GUI 프로세스의 아이콘·키 표시 검사와 캡처."""
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy
import anim_studio

print('OPERATOR_PARAMETERS', list(bpy.types.UILayout.bl_rna.functions['operator'].parameters.keys()), flush=True)
print('SHORTCUT_PROPERTIES', [(p.identifier, p.description) for p in bpy.types.UILayout.bl_rna.properties
                              if 'shortcut' in p.identifier], flush=True)
print('DISPLAY_SCALE', bpy.context.preferences.system.ui_scale, bpy.context.preferences.system.pixel_size, flush=True)
if bpy.app.background:
    raise RuntimeError('별도 GUI 프로세스로 실행해 주세요.')
anim_studio.register()
bpy.context.preferences.view.show_splash = False
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
with bpy.context.temp_override(area=area):
    bpy.ops.anim_studio.focus_panel()
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
with bpy.context.temp_override(area=area):
    bpy.ops.anim_studio.focus_panel()


def finish():
    with bpy.context.temp_override(area=area):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    stage = sys.argv[sys.argv.index('--') + 1] if '--' in sys.argv else 'before'
    path = str(Path(tempfile.gettempdir()) / ('anim_studio_icons_00d_' + stage + '.png'))
    bpy.ops.screen.screenshot(filepath=path)
    print('ICONS_SCREENSHOT', path, flush=True)
    anim_studio.unregister()
    print('ICONS_UNREGISTER_OK', flush=True)
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(finish, first_interval=1.0)
