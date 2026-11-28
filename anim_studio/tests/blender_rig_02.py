# SPDX-License-Identifier: GPL-3.0-or-later
"""실제 리그의 CTRL 배치 및 FK/IK 반대 모드 불변 검사. -- FK 또는 -- IK."""
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'Blender_Rig'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy
import anim_studio
from rig_studio import build, sync
from anim_studio.core import context, picker, pose_library as library, pose_tools as pose
from blender_picker_library_02 import check_layout
from blender_smoke import matrix_error

anim_studio.register()
arm = build.build()
mode = sys.argv[sys.argv.index('--') + 1] if '--' in sys.argv else 'FK'
for limb in json.loads(arm.data['rig_studio_info'])['limbs']:
    arm.pose.bones[limb['names']['ik']]['IK'] = float(mode == 'IK')
bpy.context.view_layer.update()
buttons = picker.rebuild(arm)
check_layout(buttons)
controls = {pb.name for pb in context.control_bones(arm)}
assert {b['bone'] for b in buttons} == controls
active, excluded = context.active_mode_controls(arm, controls)
assert not (set(n for n, _ in excluded) & picker.enabled(arm))
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode='POSE')
for pb in arm.pose.bones:
    pb.select = pb.name in controls
sync.install()
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
with bpy.context.temp_override(area=area, region=region):
    asset = library.save(bpy.context, 'Rig Pose', thumbnail=False)
target, extras = library.target(arm, asset)
assert set(target) == set(active)
all_target, _ = library.target(arm, asset, current_mode_only=False)
assert set(all_target) == controls
before = pose.current(arm, [n for n, _ in excluded])
arm.pose.bones['root'].location.x += .25
bpy.context.view_layer.update()
library.apply(arm, target, extras, key=True)
error = max((matrix_error(arm.pose.bones[n].matrix_basis, before[n]) for n in before), default=0)
assert error == 0, error
assert not sync._errors, sync._errors
from anim_studio.core import fcurve
cb = fcurve.channelbag(arm, False)
prefixes = tuple(arm.pose.bones[n].path_from_id() + '.' for n in before)
assert not any(fc.data_path.startswith(prefixes) for fc in cb.fcurves)
spine_chip = set(picker.chip_names(arm, buttons, 'Spine'))
assert spine_chip == {b['bone'] for b in buttons if b['zone'] == 'Spine'} and spine_chip, spine_chip
assert not spine_chip & {b['bone'] for b in buttons if b['zone'] in {'Head', 'COM'}}
print('RIG02_OK', mode, 'controls', len(buttons), 'overlaps', 0, 'out_of_bounds', 0, 'inactive_error', error, flush=True)

anim_studio.unregister()
