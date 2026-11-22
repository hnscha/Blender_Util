# SPDX-License-Identifier: GPL-3.0-or-later
"""실제 Rig Studio + 자동 매칭에서 01 포즈·피봇·현재 모드 검증."""
import json
import random
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'Blender_Rig'))
import bpy
import anim_studio
from rig_studio import build, sync
from anim_studio.core import context, fcurve, pose_tools as pose, space

anim_studio.register()
arm = build.build()
mode = sys.argv[sys.argv.index('--') + 1] if '--' in sys.argv else 'FK'
limbs = json.loads(arm.data['rig_studio_info'])['limbs']
for limb in limbs:
    arm.pose.bones[limb['names']['ik']]['IK'] = float(mode == 'IK')
bpy.context.view_layer.update()
controls = context.control_bones(arm)
names = [pb.name for pb in controls]
active, excluded = context.active_mode_controls(arm, names)
rng = random.Random(7522)
for frame in (0, 10, 20):
    for name in active:
        pb = arm.pose.bones[name]
        role = context.bone_meta(pb)['role']
        if role not in {'root', 'com', 'pelvis', 'spine_fk', 'clavicle', 'up_fk', 'lo_fk', 'end_fk', 'neck', 'head', 'finger', 'toe'}:
            continue
        for i in range(3):
            if not pb.lock_rotation[i]:
                pb.rotation_euler[i] = rng.uniform(-.1, .1)
        for prop in ('location', 'rotation_euler', 'scale'):
            pb.keyframe_insert(prop, frame=frame)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode='POSE')
for pb in arm.pose.bones:
    pb.select = pb.name in names
arm.data.bones.active = arm.data.bones[active[0]]
sync.install()
bpy.context.scene.frame_set(5)
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')


def op(name, **kw):
    with bpy.context.temp_override(area=area, region=region):
        assert getattr(bpy.ops.anim_studio, name)(**kw) == {'FINISHED'}, name


def matrices(names):
    return {n: (arm.matrix_world @ arm.pose.bones[n].matrix).copy() for n in names}


def compare(names, before, label, tolerance):
    actual = matrices(names)
    worst = max((abs(actual[n][i][j] - before[n][i][j]) for n in names for i in range(4) for j in range(4)), default=0)
    print('RIG01', mode, label, worst, flush=True)
    assert worst <= tolerance, (label, worst)


defs = [pb.name for pb in arm.pose.bones if context.bone_meta(pb).get('type') == 'DEF']
original, original_defs = matrices(active), matrices(defs)
op('pose_copy')
op('mirror_pose')
op('mirror_pose')
compare(active, original, 'mirror_twice_ctrl', 1e-4)
compare(defs, original_defs, 'mirror_twice_def', 1e-3)
op('tween_set', target='DEFAULT', factor=.3)
assert all(arm.pose.bones[l['names']['ik']]['IK'] == float(mode == 'IK') for l in limbs)
op('pose_paste')
compare(active, original, 'paste_local', 1e-4)
bpy.context.window_manager.anim_studio_paste_space = 'WORLD'
arm.pose.bones['root'].location.x += .5
bpy.context.view_layer.update()
op('pose_paste')
compare(active, original, 'paste_world', 1e-4)
compare(defs, original_defs, 'paste_world_def', 1e-3)

# 편집 대상 하나의 피봇 결과와 실제 DEF를 비교한다.
for pb in arm.pose.bones:
    pb.select = pb.name == 'root'
arm.data.bones.active = arm.data.bones['root']
op('temp_pivot_create')
pivot = space.pivot_state(bpy.context)[0]
pivot.location.x += .2
bpy.context.view_layer.update()
root_target, def_target = matrices(['root']), matrices(defs)
op('temp_pivot_apply')
compare(['root'], root_target, 'pivot_ctrl', 1e-4)
compare(defs, def_target, 'pivot_def', 1e-3)
assert space.pivot_state(bpy.context) is None
cb = fcurve.channelbag(arm, create=False)
prefixes = tuple(arm.pose.bones[n].path_from_id() + '.' for n, _reason in excluded)
inactive_keys = sum(len(fc.keyframe_points) for fc in cb.fcurves if fc.data_path.startswith(prefixes))
print('RIG01', mode, 'inactive_keys', inactive_keys, 'sync_errors', sync._errors, flush=True)
assert inactive_keys == 0
assert not sync._errors
anim_studio.unregister()
print('RIG_TOOLS_01_OK', mode, flush=True)
