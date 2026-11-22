# SPDX-License-Identifier: GPL-3.0-or-later
"""새 factory GUI + --enable-event-simulate 전용. 사용자 Blender에 연결하지 않는다."""
import sys
import traceback
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy
import anim_studio
from anim_studio.ui import quick_access

assert not bpy.app.background
anim_studio.register()
bpy.context.preferences.view.show_splash = False
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
window = bpy.context.window
area.spaces.active.show_region_tool_header = True
bpy.ops.object.armature_add()
bpy.ops.object.mode_set(mode='POSE')
shots = Path(__file__).resolve().parents[1] / '_shots'
shots.mkdir(exist_ok=True)


def redraw():
    with bpy.context.temp_override(area=area):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def screenshot(name):
    redraw()
    bpy.ops.screen.screenshot(filepath=str(shots / (name + '.png')))
    print('SHOT', name, [(r.type, r.x, r.width) for r in area.regions], flush=True)


with bpy.context.temp_override(area=area):
    bpy.ops.anim_studio.focus_panel()
redraw()
with bpy.context.temp_override(area=area):
    bpy.ops.anim_studio.focus_panel()


def resize(width):
    region = next(r for r in area.regions if r.type == 'UI')
    x, y = region.x, region.y + region.height // 2
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    yield
    window.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
    yield
    target_x = x + region.width - width
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=target_x, y=y)
    yield
    window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=target_x, y=y)
    yield
    print('RESIZE', width, region.width, flush=True)
    assert abs(region.width - width) < 20, (width, region.width)


def stages():
    yield
    screenshot('00e_sidebar_default')
    screenshot('00e_header')
    yield from resize(220)
    screenshot('00e_sidebar_narrow')
    yield from resize(430)
    screenshot('00e_sidebar_wide')
    # 팝오버·파이는 실제 Blender 레이아웃/그리기 경로를 사용한다.
    region = next(r for r in area.regions if r.type == 'WINDOW')
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=700, y=500)
    yield
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.wm.call_panel(name='ANIMSTUDIO_PT_pop_key', keep_open=True)
    yield
    screenshot('00e_popover')
    window.event_simulate(type='ESC', value='PRESS', x=700, y=500)
    yield
    for name, label in quick_access.PIES:
        window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=700, y=500)
        yield
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.wm.call_menu_pie(name=name)
        yield
        screenshot('00e_' + label.replace(' ', '_').lower())
        window.event_simulate(type='ESC', value='PRESS', x=700, y=500)
        yield
    assert len(quick_access._addon_keymaps) == 2
    assert all(i.type == 'NONE' for _km, i in quick_access._addon_keymaps)
    anim_studio.unregister()
    assert not quick_access._addon_keymaps
    anim_studio.register()
    redraw()
    anim_studio.unregister()
    print('LAYOUT_00E_OK', flush=True)
    bpy.ops.wm.quit_blender()


steps = stages()


def step():
    try:
        next(steps)
    except StopIteration:
        return None
    except BaseException:
        traceback.print_exc()
        print('LAYOUT_00E_FAILED', flush=True)
        bpy.ops.wm.quit_blender()
        return None
    return 0.3


bpy.app.timers.register(step, first_interval=0.5)
