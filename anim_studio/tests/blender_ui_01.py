# SPDX-License-Identifier: GPL-3.0-or-later
"""새 GUI 프로세스: 01 Undo 전수·트윈 이벤트·패널 캡처."""
import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy
import anim_studio
from anim_studio.core import fcurve, space
from blender_smoke import Engines

assert not bpy.app.background
anim_studio.register()
bpy.context.preferences.view.show_splash = False
window = bpy.context.window
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
shots = Path(__file__).resolve().parents[1] / '_shots'
shots.mkdir(exist_ok=True)


def call(name, undo=False, **kwargs):
    with bpy.context.temp_override(area=area, region=region):
        result = getattr(bpy.ops.anim_studio, name)('EXEC_DEFAULT', undo, **kwargs)
    assert result == {'FINISHED'}, (name, result)


def prepare():
    fixture = SimpleNamespace()
    Engines.setUp(fixture)
    arm = fixture.arm
    bpy.ops.object.mode_set(mode='POSE')
    for pb in arm.pose.bones:
        pb.select = pb.name in {'hand.L', 'hand.R'}
        for frame in (0, 5, 10, 20):
            pb.location.x = (frame / 30) * (1 if pb.name == 'root' else -.5)
            pb.keyframe_insert('location', frame=frame)
            pb.keyframe_insert('rotation_quaternion' if pb.rotation_mode == 'QUATERNION' else
                               'rotation_axis_angle' if pb.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler', frame=frame)
    arm.data.bones.active = arm.data.bones['hand.R']
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end, scene.use_preview_range = 0, 20, False
    scene.frame_set(5)
    wm = bpy.context.window_manager
    wm.anim_studio_history.clear()
    wm.anim_studio_align = ''
    wm.anim_studio_paste_space, wm.anim_studio_mirror_mode = 'LOCAL', 'SWAP'
    wm.anim_studio_km_world, wm.anim_studio_km_rot = False, True
    wm.anim_studio_align_pos = wm.anim_studio_align_rot = True
    wm.anim_studio_align_scale = False
    call('pose_copy')
    call('align_set')
    arm.pose.bones['hand.L'].location.x = .7
    arm.pose.bones['hand.R'].location.y = .4
    bpy.context.view_layer.update()
    return arm.name


def state(arm_name):
    arm = bpy.data.objects[arm_name]
    cb = fcurve.channelbag(arm, create=False)
    return {
        'channels': {pb.name: [(path, i, float(v)) for path, i, v in fcurve.transform_channels(pb)] for pb in arm.pose.bones},
        'keys': {(fc.data_path, fc.array_index): [tuple(p.co) for p in fc.keyframe_points]
                 for fc in cb.fcurves} if cb is not None else {},
        'constraints': {pb.name: [(c.name, c.type, c.target.name if c.target else '') for c in pb.constraints]
                        for pb in arm.pose.bones},
        'temps': sorted(o.name for o in bpy.data.objects if o.get('anim_studio_temp')),
    }


def equal(actual, expected):
    assert actual['keys'].keys() == expected['keys'].keys(), 'curve names'
    for name, values in expected['keys'].items():
        assert len(actual['keys'][name]) == len(values), (name, 'key count')
        assert all(abs(a - b) < 1e-5 for x, y in zip(actual['keys'][name], values) for a, b in zip(x, y)), name
    for name, values in expected['channels'].items():
        assert all(abs(x[2] - y[2]) < 1e-5 for x, y in zip(actual['channels'][name], values)), (name, actual['channels'][name], values)
    assert actual['constraints'] == expected['constraints'], 'constraints'
    assert actual['temps'] == expected['temps'], 'temporary objects'


def undo_checks():
    errors = []
    checks = [
        ('pose_paste', {}), ('pose_paste_opposite', {}), ('pose_paste_range', {}),
        ('mirror_pose', {}), ('mirror_range', {}), ('mirror_loop', {'half': 'FIRST'}),
        ('mirror_loop', {'half': 'SECOND'}), ('mirror_loop_single', {}),
        ('tween_set', {'target': 'DEFAULT', 'factor': .5}),
        ('key_match', {'direction': 'PREV'}), ('key_match', {'direction': 'NEXT'}),
        ('temp_pivot_create', {}), ('temp_pivot_apply', {}), ('temp_pivot_bake', {}),
        ('temp_pivot_delete', {}), ('world_lock_range', {}), ('align_now', {}), ('align_range', {}),
    ]
    for name, properties in checks:
        arm_name = prepare()
        if name in {'temp_pivot_apply', 'temp_pivot_bake', 'temp_pivot_delete'}:
            call('temp_pivot_create')
            pivot = space.pivot_state(bpy.context)[0]
            pivot.rotation_euler.z = .3
            bpy.context.view_layer.update()
        before = state(arm_name)
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.ed.undo_push(message='01 baseline')
        call(name, undo=True, **properties)
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.ed.undo()
        try:
            equal(state(arm_name), before)
        except AssertionError as error:
            errors.append((name, str(error)))
            print('UNDO01_FAILED', name, str(error), flush=True)
        else:
            print('UNDO01_OK', name, properties, flush=True)
    return errors


def shot(name):
    with bpy.context.temp_override(area=area):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    bpy.ops.screen.screenshot(filepath=str(shots / (name + '.png')))


def steps():
    yield
    undo_errors = undo_checks()
    arm_name = prepare()
    call('focus_panel')
    yield
    call('focus_panel')
    for group in ('POSE', 'KEY', 'SPACE'):
        bpy.context.window_manager.anim_studio_tab = group
        area.tag_redraw()
        yield
        shot('01_' + group.lower())
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=700, y=500)
    yield
    original = state(arm_name)
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.anim_studio.tween('INVOKE_DEFAULT', target='DEFAULT')
    yield
    assert bpy.app.driver_namespace.get('anim_studio_busy') is True
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=850, y=500)
    yield
    assert state(arm_name)['channels'] != original['channels']
    shot('01_tween_modal')
    window.event_simulate(type='ESC', value='PRESS', x=850, y=500)
    yield
    equal(state(arm_name), original)
    assert 'anim_studio_busy' not in bpy.app.driver_namespace
    print('TWEEN01_CANCEL_OK', flush=True)
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=700, y=500)
    yield
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.ed.undo_push(message='Tween baseline')
        bpy.ops.anim_studio.tween('INVOKE_DEFAULT', True, target='DEFAULT')
    yield
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=850, y=500, shift=True)
    yield
    window.event_simulate(type='RET', value='PRESS', x=850, y=500)
    yield
    assert 'anim_studio_busy' not in bpy.app.driver_namespace
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.ed.undo()
    equal(state(arm_name), original)
    print('TWEEN01_CONFIRM_UNDO_OK', flush=True)
    assert not undo_errors, undo_errors
    anim_studio.unregister()
    print('UI01_OK', flush=True)
    bpy.ops.wm.quit_blender()


runner = steps()


def tick():
    try:
        next(runner)
    except StopIteration:
        return None
    except BaseException:
        traceback.print_exc()
        print('UI01_FAILED', flush=True)
        bpy.ops.wm.quit_blender()
        return None
    return .3


bpy.app.timers.register(tick, first_interval=.5)
