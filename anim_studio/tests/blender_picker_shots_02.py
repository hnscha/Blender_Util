# SPDX-License-Identifier: GPL-3.0-or-later
"""새 GUI 프로세스: 피커 캡처 세트(넓게/좁게, 팔 FK·다리 IK / 전부 FK, 이름 규칙 리그)."""
import json
import sys
import traceback
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'Blender_Rig'))
import bpy
import anim_studio
from rig_studio import build
from anim_studio.core import picker
from anim_studio.ui import picker as picker_ui

assert not bpy.app.background
anim_studio.register()
bpy.context.preferences.view.show_splash = False
window = bpy.context.window
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
shots = Path(__file__).resolve().parents[1] / '_shots'
shots.mkdir(exist_ok=True)

GENERIC = [  # 이름 규칙 대체 배치용(일반 상용 리그와 비슷한 이름)
    ('c_traj', None), ('c_root_master.x', None), ('c_pos', None), ('c_root.x', None),
    ('c_spine_01.x', None), ('c_spine_02.x', None), ('c_spine_03.x', None),
    ('c_neck.x', None), ('c_head.x', None),
]
for side in ('l', 'r'):
    GENERIC += [(f'c_shoulder.{side}', None), (f'c_arm_fk.{side}', None), (f'c_forearm_fk.{side}', None),
                (f'c_hand_fk.{side}', None), (f'c_hand_ik.{side}', None), (f'c_arms_pole.{side}', None),
                (f'c_thigh_fk.{side}', None), (f'c_leg_fk.{side}', None), (f'c_foot_fk.{side}', None),
                (f'c_foot_ik.{side}', None), (f'c_leg_pole.{side}', None), (f'c_toes.{side}', None)]
    for finger in ('thumb', 'index', 'middle', 'ring', 'pinky'):
        GENERIC += [(f'c_{finger}{i}.{side}', None) for i in (1, 2, 3)]
    GENERIC += [(f'c_eye.{side}', None)]


def override():
    return bpy.context.temp_override(window=window, area=area, region=region)


def shot(name):
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    bpy.ops.screen.screenshot(filepath=str(shots / (name + '.png')))
    print('SHOT', name, flush=True)


def modes(arm, arm_ik, leg_ik):
    for limb in json.loads(arm.data['rig_studio_info'])['limbs']:
        arm.pose.bones[limb['names']['ik']]['IK'] = float(leg_ik if limb['part'] == 'leg' else arm_ik)
    bpy.context.view_layer.update()


def select(arm, names):
    for pb in arm.pose.bones:
        pb.select = pb.name in names


def generic_rig():
    data = bpy.data.armatures.new('GenericRig')
    obj = bpy.data.objects.new('GenericRig', data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for i, (name, _parent) in enumerate(GENERIC):
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = (i * .05, 0, 0), (i * .05, 0, .1)
    bpy.ops.object.mode_set(mode='POSE')
    return obj


def steps():
    yield
    area.spaces.active.show_region_ui = False
    arm = build.build()
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode='POSE')
    modes(arm, False, True)
    select(arm, {'hand_fk.L', 'forearm_fk.L', 'foot_ik.R'} & set(arm.pose.bones.keys()) or {arm.pose.bones[0].name})
    with override():
        bpy.ops.view3d.view_axis(type='FRONT')
        bpy.ops.view3d.view_all()
        bpy.ops.anim_studio.picker_rebuild('EXEC_DEFAULT', True)
        bpy.ops.anim_studio.picker('INVOKE_DEFAULT')
    yield
    session = picker_ui.sessions[area.as_pointer()]
    others = [b['bone'] for b in picker.layout(arm) if b['zone'] == 'Other']
    print('RIG_OTHERS', others, flush=True)
    session.rect[:] = [16, 40, 460, 640]
    area.tag_redraw()
    yield
    shot('02_picker_wide_armFK_legIK')
    session.rect[:] = [16, 40, 260, 360]
    area.tag_redraw()
    yield
    shot('02_picker_narrow_armFK_legIK')
    modes(arm, False, False)
    session.rect[:] = [16, 40, 460, 640]
    area.tag_redraw()
    yield
    shot('02_picker_wide_allFK')
    bpy.ops.object.mode_set(mode='OBJECT')
    generic = generic_rig()
    with override():
        bpy.ops.anim_studio.picker_rebuild('EXEC_DEFAULT', True)
    layout = picker.layout(generic)
    print('GENERIC_OTHERS', [b['bone'] for b in layout if b['zone'] == 'Other'], flush=True)
    print('GENERIC_ZONES', sorted({b['zone'] for b in layout}), flush=True)
    area.tag_redraw()
    yield
    shot('02_picker_generic_rig')
    with override():
        bpy.ops.anim_studio.picker('INVOKE_DEFAULT')
    yield
    anim_studio.unregister()
    print('SHOTS02_OK', flush=True)
    bpy.ops.wm.quit_blender()


runner = steps()


def tick():
    try:
        next(runner)
    except StopIteration:
        return None
    except BaseException:
        traceback.print_exc()
        print('SHOTS02_FAILED', flush=True)
        bpy.ops.wm.quit_blender()
        return None
    return .5


bpy.app.timers.register(tick, first_interval=.5)
