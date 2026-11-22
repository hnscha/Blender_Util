# SPDX-License-Identifier: GPL-3.0-or-later
import sys
from pathlib import Path
from types import SimpleNamespace
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy
from bpy.props import StringProperty
from anim_studio.core import fcurve
from blender_smoke import Engines


class AS_OT_probe(bpy.types.Operator):
    bl_idname = 'anim_studio.undo_probe'
    bl_label = 'Undo Probe'
    bl_options = {'REGISTER', 'UNDO'}
    kind: StringProperty()

    def execute(self, context):
        arm = context.active_object
        pb = arm.pose.bones['hand.L']
        path = pb.path_from_id('location')
        if 'frame' in self.kind:
            context.scene.frame_set(0)
            context.scene.frame_set(10)
            context.scene.frame_set(5)
        if 'native' in self.kind:
            for frame, value in ((0, 3), (10, 7)):
                pb.location.x = value
                arm.keyframe_insert(path, index=0, frame=frame)
        if 'bulk' in self.kind:
            fcurve.write_keys(fcurve.channelbag(arm), path, 0, [0, 10], [3, 7], pb.name)
        pb.location.x = .7
        arm.update_tag(refresh={'OBJECT'})
        context.view_layer.update()
        pb.location.x = .7
        return {'FINISHED'}


bpy.utils.register_class(AS_OT_probe)
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
for kind in ('frame', 'native', 'bulk', 'frame_native', 'frame_bulk'):
    fixture = SimpleNamespace()
    Engines.setUp(fixture)
    arm = fixture.arm
    name = arm.name
    pb = arm.pose.bones['hand.L']
    for frame in (0, 10):
        pb.keyframe_insert('location', frame=frame)
    bpy.context.scene.frame_set(5)
    pb.location.x = .7
    bpy.context.view_layer.update()
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.ed.undo_push(message='Probe baseline')
        bpy.ops.anim_studio.undo_probe('EXEC_DEFAULT', True, kind=kind)
        bpy.ops.ed.undo()
    print('UNDO_PROBE', kind, bpy.data.objects[name].pose.bones['hand.L'].location.x, flush=True)
bpy.ops.wm.quit_blender()
