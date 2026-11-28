# SPDX-License-Identifier: GPL-3.0-or-later
"""새 GUI 프로세스(--enable-event-simulate): 피커 이벤트·Undo·포즈 라이브러리 모달·캡처."""
import json
import sys
import traceback
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'Blender_Rig'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy
import anim_studio
from rig_studio import build
from anim_studio.core import context as studio_context, picker, pose_library as library, pose_tools as pose
from anim_studio.ui import picker as picker_ui
from blender_smoke import matrix_error

assert not bpy.app.background
anim_studio.register()
bpy.context.preferences.view.show_splash = False
window = bpy.context.window
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
shots = Path(__file__).resolve().parents[1] / '_shots'
shots.mkdir(exist_ok=True)
arm = None


def override():
    return bpy.context.temp_override(window=window, area=area, region=region)


def shot(name):
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    bpy.ops.screen.screenshot(filepath=str(shots / (name + '.png')))


def selected():
    return {pb.name for pb in arm.pose.bones if pb.select}


def session():
    return picker_ui.sessions.get(area.as_pointer())


def geometry():
    with override():
        return session().metrics(bpy.context)


def button_point(bone):
    _scale, _rect, body, _chips = geometry()
    button = next(b for b in picker.layout(arm) if b['bone'] == bone)
    x, y, w, h = picker_ui.PickerSession.button_rect(button, body)
    return int(region.x + x + w / 2), int(region.y + y + h / 2)


def chip_point(name):
    _scale, _rect, _body, chips = geometry()
    x, y, w, h = next(r for chip, r in chips if chip == name)
    return int(region.x + x + w / 2), int(region.y + y + h / 2)


def click(point, **mods):
    x, y = point
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y, **mods)
    window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y, **mods)


def drag(a, b, **mods):
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=a[0], y=a[1])
    window.event_simulate(type='LEFTMOUSE', value='PRESS', x=a[0], y=a[1], **mods)
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=(a[0] + b[0]) // 2, y=(a[1] + b[1]) // 2, **mods)
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=b[0], y=b[1], **mods)
    window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=b[0], y=b[1], **mods)


def expect(label, actual, wanted):
    assert actual == set(wanted), (label, sorted(actual), sorted(wanted))
    print('PICKER02_GUI', label, 'OK', len(actual), flush=True)


def steps():
    global arm
    yield
    arm = build.build()
    arm_name = arm.name
    for limb in json.loads(arm.data['rig_studio_info'])['limbs']:
        arm.pose.bones[limb['names']['ik']]['IK'] = 1.0 if limb['part'] == 'leg' else 0.0
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode='POSE')
    for pb in arm.pose.bones:
        pb.select = False
    area.spaces.active.show_region_ui = True
    wm = bpy.context.window_manager
    wm.anim_studio_tab = 'POSE'
    with override():
        bpy.ops.view3d.view_axis(type='FRONT')
        bpy.ops.view3d.view_all()
        assert bpy.ops.anim_studio.picker_rebuild('EXEC_DEFAULT', True) == {'FINISHED'}
        assert bpy.ops.anim_studio.picker('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
    yield
    yield
    assert session() is not None
    enabled = picker.enabled(arm)
    buttons = picker.layout(arm)
    first = next(b['bone'] for b in buttons if b['zone'] == 'Arm L' and b['bone'] in enabled)
    second = next(b['bone'] for b in buttons if b['zone'] == 'Arm R' and b['bone'] in enabled)
    disabled = next((b['bone'] for b in buttons if b['bone'] not in enabled), None)

    click(button_point(first))
    yield
    expect('click', selected(), [first])
    assert arm.data.bones.active.name == first
    click(button_point(second), shift=True)
    yield
    expect('shift_click', selected(), [first, second])
    shot('02_picker')
    click(button_point(first), ctrl=True)
    yield
    expect('ctrl_click', selected(), [second])
    if disabled:
        click(button_point(disabled), shift=True)
        yield
        expect('disabled_click', selected(), [second])
    with override():
        bpy.ops.ed.undo_push(message='picker baseline')
    click(button_point(first))
    yield
    expect('before_undo', selected(), [first])
    with override():
        bpy.ops.ed.undo()
    yield
    arm = bpy.data.objects[arm_name]
    expect('undo', selected(), [second])
    assert session() is not None

    click(chip_point('Arm L'))
    yield
    expect('chip_arm_l', selected(), [b['bone'] for b in buttons if b['zone'] == 'Arm L' and b['bone'] in enabled])
    click(chip_point('Mirror'))
    yield
    mapping = picker.mirror.pairs(arm)
    expect('chip_mirror', selected(),
           [mapping[b['bone']] for b in buttons if b['zone'] == 'Arm L' and b['bone'] in enabled])
    leg_l = [b for b in buttons if b['zone'] == 'Leg L' and b['bone'] in enabled]
    _scale, _rect, body, _chips = geometry()
    rects = [picker_ui.PickerSession.button_rect(b, body) for b in leg_l]
    every = [picker_ui.PickerSession.button_rect(b, body) for b in buttons]

    def empty(px, py):
        return not any(picker_ui.contains(r, px - region.x, py - region.y) for r in every)

    # 박스는 빈 곳에서 시작해야 한다. 다리 묶음을 감싸는 모서리 중 빈 곳을 찾는다.
    x0, x1 = min(r[0] for r in rects), max(r[0] + r[2] for r in rects)
    y0, y1 = min(r[1] for r in rects), max(r[1] + r[3] for r in rects)
    start = None
    for pad in range(2, 14, 2):
        for cx, cy, ox, oy in ((x1, y1, x0, y0), (x0, y1, x1, y0), (x1, y0, x0, y1), (x0, y0, x1, y1)):
            sx = int(region.x + cx + (pad if cx == x1 else -pad))
            sy = int(region.y + cy + (pad if cy == y1 else -pad))
            if empty(sx, sy):
                start = (sx, sy), (int(region.x + ox + (pad if ox == x1 else -pad)),
                                   int(region.y + oy + (pad if oy == y1 else -pad)))
                break
        if start:
            break
    assert start, 'no empty corner for box'
    lo, hi = start
    drag(lo, hi)
    yield
    boxed = picker.box_names(buttons, tuple(v for v in (
        (lo[0] - region.x - body[0]) / body[2], (lo[1] - region.y - body[1]) / body[3],
        (hi[0] - region.x - body[0]) / body[2], (hi[1] - region.y - body[1]) / body[3])))
    expect('box', selected(), [n for n in boxed if n in enabled])
    assert {b['bone'] for b in leg_l} <= selected()

    # 피커 밖 클릭과 키보드는 3D 뷰로 통과한다.
    outside = (region.x + region.width - 60, region.y + region.height // 2)
    click(outside)
    yield
    expect('outside_click_passes', selected(), [])
    assert session() is not None
    point = button_point(first)
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=point[0], y=point[1])
    window.event_simulate(type='A', value='PRESS', x=point[0], y=point[1])
    window.event_simulate(type='A', value='RELEASE', x=point[0], y=point[1])
    yield
    assert len(selected()) > 10, len(selected())
    print('PICKER02_GUI keyboard_passes OK', len(selected()), flush=True)

    # 포즈 라이브러리: 저장(썸네일) → 다른 자세 → Apply → Blend 모달 Esc/확정 → Undo.
    controls = sorted(b['bone'] for b in buttons if b['zone'] in {'Arm L', 'Arm R'} and b['bone'] in enabled)
    for pb in arm.pose.bones:
        pb.select = pb.name in controls
    hand = arm.pose.bones[first]
    hand.rotation_mode = 'QUATERNION' if hand.rotation_mode == 'QUATERNION' else hand.rotation_mode
    hand.location = (.05, .02, -.03)
    hand.rotation_euler = (.3, -.2, .1) if hand.rotation_mode not in {'QUATERNION', 'AXIS_ANGLE'} else hand.rotation_euler
    if hand.rotation_mode == 'QUATERNION':
        hand.rotation_quaternion = (.95, .2, -.1, .2)
    bpy.context.view_layer.update()
    saved = pose.current(arm, controls)
    with override():
        assert bpy.ops.anim_studio.asset_save('EXEC_DEFAULT', True, pose_name='GUI Pose') == {'FINISHED'}
    asset = bpy.data.actions['GUI Pose']
    assert asset.preview is not None and asset.preview.icon_id, 'thumbnail'
    asset_size = tuple(asset.preview.image_size)
    print('POSELIB02_GUI thumbnail OK', asset_size, flush=True)
    for name in controls:
        arm.pose.bones[name].matrix_basis = arm.pose.bones[name].matrix_basis.Identity(4)
    bpy.context.view_layer.update()
    rest = pose.current(arm, controls)
    wm.anim_studio_asset_index = bpy.data.actions.find('GUI Pose')
    with override():
        bpy.ops.ed.undo_push(message='apply baseline')
        assert bpy.ops.anim_studio.asset_apply('EXEC_DEFAULT', True) == {'FINISHED'}
    error = max(matrix_error(arm.pose.bones[n].matrix_basis, saved[n]) for n in controls)
    assert error <= 1e-4, error
    print('POSELIB02_GUI apply OK', error, flush=True)
    with override():
        bpy.ops.ed.undo()
    yield
    arm = bpy.data.objects[arm_name]
    error = max(matrix_error(arm.pose.bones[n].matrix_basis, rest[n]) for n in controls)
    assert error <= 1e-4, ('apply undo', error)
    print('POSELIB02_GUI apply_undo OK', error, flush=True)

    cx, cy = region.x + region.width // 2, region.y + region.height // 2
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=cx, y=cy)
    with override():
        assert bpy.ops.anim_studio.asset_blend('INVOKE_DEFAULT', True) == {'RUNNING_MODAL'}
    yield
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=cx + 150, y=cy)
    yield
    moved = max(matrix_error(arm.pose.bones[n].matrix_basis, rest[n]) for n in controls)
    assert moved > 1e-4, 'blend preview'
    shot('02_poselib_blend')
    window.event_simulate(type='ESC', value='PRESS', x=cx + 150, y=cy)
    yield
    error = max(matrix_error(arm.pose.bones[n].matrix_basis, rest[n]) for n in controls)
    assert error <= 1e-6, ('blend esc', error)
    print('POSELIB02_GUI blend_esc OK', error, flush=True)
    with override():
        bpy.ops.ed.undo_push(message='blend baseline')
        assert bpy.ops.anim_studio.asset_blend('INVOKE_DEFAULT', True) == {'RUNNING_MODAL'}
    yield
    window.event_simulate(type='LEFTMOUSE', value='PRESS', x=cx, y=cy)
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=cx + 150, y=cy)
    window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=cx + 150, y=cy)
    yield
    half = pose.blend(rest, saved, .5)
    error = max(matrix_error(arm.pose.bones[n].matrix_basis, half[n]) for n in controls)
    assert error <= 1e-3, ('blend 0.5', error)
    print('POSELIB02_GUI blend_confirm OK', error, flush=True)
    with override():
        bpy.ops.ed.undo()
    yield
    arm = bpy.data.objects[arm_name]
    error = max(matrix_error(arm.pose.bones[n].matrix_basis, rest[n]) for n in controls)
    assert error <= 1e-4, ('blend undo', error)
    print('POSELIB02_GUI blend_undo OK', error, flush=True)
    assert not library_ops_sessions(), 'blend sessions'

    # 리뷰 3: 배치가 손상돼도 피커 밖·안 마우스 이동으로 닫히지 않고, Rebuild 로 복구된다.
    good = arm.data[picker.PROPERTY]
    arm.data[picker.PROPERTY] = 'broken'
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=outside[0], y=outside[1])
    yield
    inside_point = (int(region.x + body[0] + body[2] / 2), int(region.y + body[1] + body[3] / 2))
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=inside_point[0], y=inside_point[1])
    yield
    assert session() is not None, 'picker closed on corrupt layout'
    with override():
        assert bpy.ops.anim_studio.picker_rebuild('EXEC_DEFAULT', True) == {'FINISHED'}
    assert picker.layout(arm)
    print('PICKER02_GUI corrupt_layout_keeps_picker OK', flush=True)
    assert tuple(asset_size) == (asset_size[0], asset_size[0]), asset_size
    with override():
        bpy.ops.anim_studio.picker('INVOKE_DEFAULT')
    yield
    assert session() is None and not picker_ui.sessions
    with override():
        bpy.ops.anim_studio.focus_panel()
    yield
    with override():
        bpy.ops.anim_studio.focus_panel()
    wm.anim_studio_tab = 'POSE'
    area.tag_redraw()
    yield
    shot('02_poselib')
    anim_studio.unregister()
    print('PICKER02_GUI_OK', flush=True)
    bpy.ops.wm.quit_blender()


def library_ops_sessions():
    from anim_studio.ops import library_ops
    return library_ops._sessions


runner = steps()


def tick():
    try:
        next(runner)
    except StopIteration:
        return None
    except BaseException:
        traceback.print_exc()
        print('PICKER02_GUI_FAILED', flush=True)
        bpy.ops.wm.quit_blender()
        return None
    return .4


bpy.app.timers.register(tick, first_interval=.5)
