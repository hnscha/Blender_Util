# SPDX-License-Identifier: GPL-3.0-or-later
"""새 --factory-startup GUI 프로세스에서만 실행하는 패널 API 검사."""
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Quaternion

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import anim_studio
from anim_studio.core import fcurve

if bpy.app.background:
    raise RuntimeError('이 검사는 별도 GUI Blender에서 실행합니다.')

anim_studio.register()
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.view.smooth_view = 0
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')


def redraw():
    with bpy.context.temp_override(area=area):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


with bpy.context.temp_override(area=area):
    assert bpy.ops.anim_studio.focus_panel() == {'FINISHED'}
region = next(r for r in area.regions if r.type == 'UI')
redraw()
with bpy.context.temp_override(area=area):
    assert bpy.ops.anim_studio.focus_panel() == {'FINISHED'}
assert region.active_panel_category == 'Anim Studio'
redraw()
for tool in ('MOVE', 'ROTATE', 'SCALE'):
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.anim_studio.tool_orientation(tool=tool) == {'FINISHED'}
        before = bpy.context.scene.transform_orientation_slots[0].type
        assert bpy.ops.anim_studio.tool_orientation(tool=tool) == {'FINISHED'}
        assert before != bpy.context.scene.transform_orientation_slots[0].type
for axis in ('X', 'Y', 'Z'):
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.anim_studio.view_flip(axis=axis) == {'FINISHED'}
        first = area.spaces.active.region_3d.view_rotation.copy()
        assert bpy.ops.anim_studio.view_flip(axis=axis) == {'FINISHED'}
        second = area.spaces.active.region_3d.view_rotation.copy()
        assert abs(first.dot(second)) < 1e-4
        assert bpy.ops.anim_studio.view_flip(axis=axis) == {'FINISHED'}
        assert abs(first.dot(area.spaces.active.region_3d.view_rotation)) > 0.99999
        # 같은 시선이라도 롤이 다르면 첫 축 뷰로 돌아가야 한다.
        area.spaces.active.region_3d.view_rotation = first @ Quaternion((0, 0, 1), 0.3)
        assert bpy.ops.anim_studio.view_flip(axis=axis) == {'FINISHED'}
        assert abs(first.dot(area.spaces.active.region_3d.view_rotation)) > 0.99999
redraw()


def finish():
    # 테스트 프로세스에서만 UI 펼침 애니메이션 뒤 한 번 캡처한다.
    with bpy.context.temp_override(area=area):
        assert area.spaces.active.show_region_ui and region.width > 1
        bpy.ops.ed.undo_push(message='Anim Studio test baseline')
        before = bpy.context.scene.render.resolution_x
        bpy.ops.anim_studio.easy_resolution('EXEC_DEFAULT', True)
        mesh_before = area.spaces.active.show_object_viewport_mesh
        bpy.ops.anim_studio.toggle_type_visibility('EXEC_DEFAULT', True, kind='MESH')
        bpy.ops.ed.undo()
        assert bpy.context.scene.render.resolution_x == before
        assert area.spaces.active.show_object_viewport_mesh != mesh_before
        print('DATA_UNDO_AND_SCREEN_NO_UNDO_OK')
        area.spaces.active.show_object_viewport_mesh = mesh_before
        screenshot = str(Path(tempfile.gettempdir()) / 'anim_studio_ui_smoke.png')
        bpy.ops.screen.screenshot(filepath=screenshot, hide_props_region=False)
        print('UI_SCREENSHOT', screenshot)
        # 키 기록/삭제는 미기록 자세를 유지하고 Undo 한 번으로 커브를 복원한다.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from blender_smoke import Engines
        from types import SimpleNamespace
        fixture = SimpleNamespace()
        Engines.setUp(fixture)
        arm_name = fixture.arm.name
        arm = bpy.data.objects[arm_name]
        for pb in arm.pose.bones:
            for path, index, value in fcurve.transform_channels(pb):
                arm.keyframe_insert(path, index=index, frame=5, group=pb.name)
        bpy.context.scene.frame_set(5)
        bpy.ops.object.mode_set(mode='POSE')
        bpy.ops.wm.tool_set_by_id(name='builtin.move')
        for pb in arm.pose.bones:
            pb.select = pb.name == 'root'
        arm.data.bones.active = arm.data.bones['root']
        arm.pose.bones['root'].location.y = 1.25
        arm.pose.bones['root'].rotation_quaternion = (0.70710678, 0.70710678, 0, 0)
        arm.pose.bones['hand.L'].location.x = 3.0
        bpy.context.view_layer.update()
        for operator in ('key_tool_channels', 'delete_tool_channels', 'key_all_controls'):
            arm = bpy.data.objects[arm_name]
            arm.pose.bones['root'].location.y = 1.25
            arm.pose.bones['root'].rotation_quaternion = (0.70710678, 0.70710678, 0, 0)
            arm.pose.bones['hand.L'].location.x = 3.0
            bpy.context.view_layer.update()
            bpy.ops.ed.undo_push(message='Key pose baseline')
            assert getattr(bpy.ops.anim_studio, operator)('EXEC_DEFAULT', True) == {'FINISHED'}
            assert abs(arm.pose.bones['root'].rotation_quaternion.x - 0.70710678) < 1e-6
            assert arm.pose.bones['hand.L'].location.x == 3.0
            bpy.ops.ed.undo()
            arm = bpy.data.objects[arm_name]
            cb = fcurve.channelbag(arm, create=False)
            assert cb.fcurves.find('pose.bones["root"].location', index=1).evaluate(5) == 0.0
            assert abs(arm.pose.bones['root'].rotation_quaternion.x - 0.70710678) < 1e-6
            assert arm.pose.bones['hand.L'].location.x == 3.0
            print('KEY_UNDO_POSE', operator, arm.pose.bones['root'].rotation_quaternion.x,
                  arm.pose.bones['hand.L'].location.x, flush=True)
        print('KEY_OPERATORS_SINGLE_UNDO_OK')
        arm = bpy.data.objects[arm_name]
        arm.pose.bones['root'].rotation_quaternion = (0.70710678, 0.70710678, 0, 0)
        arm.pose.bones['hand.L'].location.x = 3.0
        bpy.context.view_layer.update()
        bpy.ops.ed.undo_push(message='Native key baseline')
        assert bpy.ops.anim.keyframe_insert_by_name('EXEC_DEFAULT', True, type='LocRotScale') == {'FINISHED'}
        bpy.ops.ed.undo()
        arm = bpy.data.objects[arm_name]
        print('NATIVE_KEY_UNDO_POSE', arm.pose.bones['root'].rotation_quaternion.x,
              arm.pose.bones['hand.L'].location.x, flush=True)
        arm.pose.bones['root'].rotation_quaternion = (0.70710678, 0.70710678, 0, 0)
        arm.pose.bones['hand.L'].location.x = 3.0
        bpy.context.view_layer.update()
        bpy.ops.ed.undo_push(message='Native delete baseline')
        assert bpy.ops.anim.keyframe_delete_v3d('EXEC_DEFAULT', True) == {'FINISHED'}
        bpy.ops.ed.undo()
        arm = bpy.data.objects[arm_name]
        print('NATIVE_DELETE_UNDO_POSE', arm.pose.bones['root'].rotation_quaternion.x,
              arm.pose.bones['hand.L'].location.x, flush=True)
        assert bpy.ops.anim_studio.open_keymap() == {'FINISHED'}
        preferences = [a.spaces.active for w in bpy.context.window_manager.windows
                       for a in w.screen.areas if a.type == 'PREFERENCES']
        assert preferences and all(space.filter_text == 'anim_studio' for space in preferences)
    anim_studio.unregister()
    print('UI_SMOKE_OK', flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(finish, first_interval=0.5)
