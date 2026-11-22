# SPDX-License-Identifier: GPL-3.0-or-later
"""실제 Rig Studio + 자동 매칭 활성 상태의 00d 베이크 검증."""
import sys
import random
import json
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'Blender_Rig'))

import bpy
from rig_studio import build, sync
from anim_studio.core import bake, context, fcurve

arm = build.build()
controls = context.control_bones(arm)
mode = sys.argv[sys.argv.index('--') + 1] if '--' in sys.argv else 'FK'
for limb in json.loads(arm.data['rig_studio_info'])['limbs']:
    arm.pose.bones[limb['names']['ik']]['IK'] = float(mode == 'IK')
bpy.context.view_layer.update()
fk_roles = {'root', 'com', 'pelvis', 'spine_fk', 'neck', 'head',
            'clavicle', 'up_fk', 'lo_fk', 'end_fk', 'finger', 'toe'}
rng = random.Random(240522)
for frame in (0, 37, 100):
    for pb in controls:
        role = context.bone_meta(pb)['role']
        if role not in fk_roles or (mode == 'IK' and role in sync.FKR):
            continue
        for index in range(3):
            if not pb.lock_rotation[index]:
                pb.rotation_euler[index] = rng.uniform(-0.22, 0.22)
        for channel in ('location', 'rotation_euler', 'scale'):
            pb.keyframe_insert(channel, frame=frame)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode='POSE')
sync.install()
frames = list(range(101))
defs = [pb for pb in arm.pose.bones if context.bone_meta(pb).get('type') == 'DEF']


def measure():
    result = []
    for frame in frames:
        bpy.context.scene.frame_set(frame)
        result.append([v for pb in defs for row in arm.matrix_world @ pb.matrix for v in row])
    return result


before = measure()
result = bake.bake(arm, [pb.name for pb in controls], frames)
after = measure()
error = max(abs(a - b) for x, y in zip(before, after) for a, b in zip(x, y))
cb = fcurve.channelbag(arm)
inactive = [pb for pb in controls if context.bone_meta(pb)['role'] in (sync.IKR if mode == 'FK' else sync.FKR)]
prefixes = tuple(pb.path_from_id() + '.' for pb in inactive)
keys = sum(len(fc.keyframe_points) for fc in cb.fcurves if fc.data_path.startswith(prefixes))
print('REAL_MODES', mode, 'DEF', error, 'baked', len(result.baked), 'inactive_keys', keys,
      'handlers', sync._on_frame_change in bpy.app.handlers.frame_change_post,
      sync._on_depsgraph in bpy.app.handlers.depsgraph_update_post, 'sync_errors', sync._errors, flush=True)
assert error <= 1e-3, error
assert keys == 0, keys
assert not sync._errors, sync._errors
