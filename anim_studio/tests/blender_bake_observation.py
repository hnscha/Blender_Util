# SPDX-License-Identifier: GPL-3.0-or-later
"""00c 10번 관찰 전용. 실제 리그 소스와 파일은 변경하지 않는다."""
import random
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'Blender_Rig'))

import bpy
from rig_studio import build
from anim_studio.core import bake, context, fcurve

arm = build.build()
controls = context.control_bones(arm)
fk_roles = {'root', 'com', 'pelvis', 'spine_fk', 'neck', 'head',
            'clavicle', 'up_fk', 'lo_fk', 'end_fk', 'finger', 'toe'}
rng = random.Random(240522)
for frame in (0, 37, 100):
    for pb in controls:
        if context.bone_meta(pb)['role'] not in fk_roles:
            continue
        for index in range(3):
            if not pb.lock_rotation[index]:
                pb.rotation_euler[index] = rng.uniform(-0.22, 0.22)
        for channel in ('location', 'rotation_euler', 'scale'):
            pb.keyframe_insert(channel, frame=frame)
frames = list(range(101))
defs = [pb for pb in arm.pose.bones if context.bone_meta(pb).get('type') == 'DEF']


def measure():
    result = []
    for frame in frames:
        bpy.context.scene.frame_set(frame)
        result.append((
            [v for pb in controls for row in arm.matrix_world @ pb.matrix for v in row],
            [v for pb in defs for row in arm.matrix_world @ pb.matrix for v in row],
            [value for pb in controls for _path, _index, value in fcurve.transform_channels(pb)],
        ))
    return result


def difference(a, b):
    return tuple(max(abs(x - y) for af, bf in zip(a, b) for x, y in zip(af[i], bf[i]))
                 for i in range(3))


original = measure()
print('OBS_REPLAY', difference(original, measure()), flush=True)
first_result = bake.bake(arm, [pb.name for pb in controls], frames)
first = measure()
second_result = bake.bake(arm, [pb.name for pb in controls], frames)
second = measure()
print('OBS_BAKE_FIRST', difference(original, first), 'baked', len(first_result.baked),
      'skipped', len(first_result.skipped), flush=True)
print('OBS_BAKE_SECOND', difference(first, second), 'baked', len(second_result.baked),
      'skipped', len(second_result.skipped), flush=True)
print('OBS_HANDLERS', len(bpy.app.handlers.frame_change_post),
      len(bpy.app.handlers.depsgraph_update_post), flush=True)
