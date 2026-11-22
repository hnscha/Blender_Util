# SPDX-License-Identifier: GPL-3.0-or-later
"""별도 Blender --background --factory-startup --python-exit-code 1 --python 실행용."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import anim_studio
from anim_studio.core import bake, context, fcurve, mirror, snapshot, temp
from anim_studio.ops import hotkey_ops
from anim_studio.ui import panels


def matrix_error(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))


class Engines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        anim_studio.register()

    @classmethod
    def tearDownClass(cls):
        anim_studio.unregister()

    def setUp(self):
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        data = bpy.data.armatures.new('SmokeRig')
        self.arm = bpy.data.objects.new('SmokeRig', data)
        bpy.context.scene.collection.objects.link(self.arm)
        bpy.context.view_layer.objects.active = self.arm
        self.arm.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        for name, x, parent in [('root', 0, None), ('hand.L', 1, 'root'),
                                ('hand.R', -1, 'root'), ('tip.L', 1, 'hand.L')]:
            bone = data.edit_bones.new(name)
            bone.head = (x, 0 if parent != 'hand.L' else 1, 0)
            bone.tail = (x, 1 if parent != 'hand.L' else 2, 0)
            if parent:
                bone.parent = data.edit_bones[parent]
        bpy.ops.object.mode_set(mode='OBJECT')
        self.arm.pose.bones['hand.L'].rotation_mode = 'XYZ'
        self.arm.pose.bones['hand.R'].rotation_mode = 'XYZ'
        self.arm.pose.bones['tip.L'].rotation_mode = 'AXIS_ANGLE'
        self.arm.matrix_world = Matrix.Translation((2, 3, 4))
        bpy.context.view_layer.update()

    def test_bulk_keys_locks_drivers_and_ranges(self):
        arm = self.arm
        pb = arm.pose.bones['hand.L']
        cb = fcurve.channelbag(arm)
        path = pb.path_from_id('location')
        fcurve.write_keys(cb, path, 0, [-5, 1, 4, 9, 20], [1, 2, 3, 4, 5], pb.name)
        fcurve.write_keys(cb, path, 0, [1, 2, 9], [10, 20, 30], pb.name)
        curve = cb.fcurves.find(path, index=0)
        self.assertEqual([tuple(p.co) for p in curve.keyframe_points],
                         [(-5, 1), (1, 10), (2, 20), (9, 30), (20, 5)])
        pb.lock_location[1] = True
        self.assertTrue(fcurve.write_keys(cb, path, 1, [1], [99], pb.name))
        driver = arm.driver_add(path, 2)
        driver.driver.expression = '0.0'
        self.assertTrue(fcurve.write_keys(cb, path, 2, [1], [99], pb.name))
        self.assertIsNone(cb.fcurves.find(path, index=1))
        self.assertIsNone(cb.fcurves.find(path, index=2))
        self.assertEqual(fcurve.delete_keys_outside(cb, 1, 9), 2)
        self.assertEqual(fcurve.delete_keys(cb, path, 0, 2, 2), 1)
        self.assertEqual([p.co.x for p in curve.keyframe_points], [1, 9])
        self.assertEqual(len(arm.animation_data.action.slots), 1)

    def test_snapshot_and_mirror(self):
        arm = self.arm
        left, right, root = (arm.pose.bones[n] for n in ('hand.L', 'hand.R', 'root'))
        left.location = (0.3, 0.2, 0.1)
        left.rotation_euler = (0.1, -0.3, 0.25)
        left.scale = (1.1, 0.9, 1.2)
        root.location.x = 0.4
        left['ik'] = 0.25
        right['ik'] = 0.75
        left['enabled'] = True
        right['enabled'] = False
        left['ignored'] = 'text'
        bpy.context.view_layer.update()
        bones = ['root', 'hand.L', 'hand.R']
        snap = snapshot.capture(arm, bones)
        json.dumps(snap)
        json.dumps(snapshot.rest_record(arm))
        before = {name: arm.pose.bones[name].matrix.copy() for name in bones}
        targets = mirror.mirror_pose(arm, bones)
        self.assertAlmostEqual(left['ik'], 0.25)  # 계산에는 부작용이 없다.
        snapshot.apply(arm, targets)
        self.assertAlmostEqual(right['ik'], 0.25)
        self.assertEqual(right['enabled'], True)
        snapshot.apply(arm, mirror.mirror_pose(arm, bones))
        for name in bones:
            self.assertLess(matrix_error(before[name], arm.pose.bones[name].matrix), 1e-5)
        snapshot.apply(arm, snap)
        self.assertAlmostEqual(left['ik'], 0.25)
        world = snapshot.capture(arm, bones, 'WORLD')
        root.location = (0, 0, 0)
        bpy.context.view_layer.update()
        snapshot.apply(arm, world)
        self.assertLess(matrix_error(before['hand.L'], left.matrix), 1e-5)
        directional = mirror.mirror_pose(arm, ['hand.L'], 'LEFT_TO_RIGHT')
        self.assertEqual(set(directional), {'hand.R'})

    def test_bake_roundtrip_and_failure_restore(self):
        arm = self.arm
        names = [pb.name for pb in arm.pose.bones]
        cb = fcurve.channelbag(arm)
        for number, pb in enumerate(arm.pose.bones):
            for path, index, value in fcurve.transform_channels(pb):
                values = [value] * 3
                if path.endswith('location'):
                    values = [number * 0.1, 0.2 * (index + 1), -0.15 * (index + 1)]
                elif path.endswith('rotation_euler'):
                    values = [0.1 * index, 0.3 * (index + 1), -0.2 * (index + 1)]
                elif path.endswith('rotation_quaternion'):
                    values = [1, 0.9, 0.8] if index == 0 else [0, 0.1 * index, 0.15 * index]
                elif path.endswith('rotation_axis_angle') and index == 0:
                    values = [0, 0.7, 1.3]
                elif path.endswith('scale'):
                    values = [1, 1.1, 0.9]
                fcurve.write_keys(cb, path, index, [1, 6, 12], values, pb.name)
        frames = list(range(1, 13))
        expected = {}
        for frame in frames:
            bpy.context.scene.frame_set(frame)
            expected[frame] = {pb.name: (arm.matrix_world @ pb.matrix).copy() for pb in arm.pose.bones}
        bpy.context.scene.frame_set(3, subframe=0.5)
        result = bake.bake(arm, names, frames)
        self.assertEqual(set(result.baked), set(names))
        self.assertEqual(result.skipped, [])
        self.assertEqual(bpy.context.scene.frame_current_final, 3.5)
        self.assertNotIn('anim_studio_busy', bpy.app.driver_namespace)
        worst = 0
        for frame in frames:
            bpy.context.scene.frame_set(frame)
            for pb in arm.pose.bones:
                worst = max(worst, matrix_error(expected[frame][pb.name], arm.matrix_world @ pb.matrix))
        print('BAKE_MAX_ERROR', worst)
        self.assertLess(worst, 1e-5)
        bpy.context.scene.frame_set(3, subframe=0.5)
        def failure(_frame):
            raise ValueError('측정 실패 검사')
        with self.assertRaises(ValueError):
            bake.bake(arm, names, [1, 2], target_fn=failure)
        self.assertEqual(bpy.context.scene.frame_current_final, 3.5)
        self.assertNotIn('anim_studio_busy', bpy.app.driver_namespace)

    def test_temp_lifecycle_and_constraint_bake(self):
        arm = self.arm
        pb = arm.pose.bones['hand.L']
        with self.assertRaises(ValueError):
            with temp.session() as session:
                target = session.empty('Temporary', Matrix.Identity(4))
                session.constraint(pb, 'COPY_TRANSFORMS', target=target)
                raise ValueError('정리 검사')
        self.assertEqual(len(pb.constraints), 0)
        self.assertNotIn(temp.COLLECTION, bpy.data.collections)
        with temp.session() as session:
            target = session.empty('Temporary', Matrix.Translation((5, 0, 0)))
            session.constraint(pb, 'COPY_TRANSFORMS', target=target, owner_space='WORLD', target_space='WORLD')
            bpy.context.view_layer.update()
            expected = (arm.matrix_world @ pb.matrix).copy()
            result = bake.bake(arm, [pb.name], [1, 2])
            self.assertEqual(result.baked, [pb.name])
            self.assertEqual(len(pb.constraints), 0)
            bpy.context.scene.frame_set(1)
            self.assertLess(matrix_error(expected, arm.matrix_world @ pb.matrix), 1e-5)
        with temp.session() as session:
            obj = session.empty('Keep', Matrix.Identity(4))
            obj.name = 'Renamed'
            session.keep()
        self.assertEqual(temp.cleanup_all(), 1)
        self.assertNotIn(temp.COLLECTION, bpy.data.collections)
        constraint = pb.constraints.new('LIMIT_LOCATION')
        result = bake.bake(arm, [pb.name], [1, 2])
        self.assertFalse(result.baked)
        self.assertIn('컨스트레인트', result.skipped[0][1])
        pb.constraints.remove(constraint)
        with self.assertRaises(ValueError):
            with temp.session() as session:
                target = session.empty('DeleteOwner', Matrix.Identity(4))
                target.name = 'RenamedTarget'
                session.constraint(pb, 'COPY_LOCATION', target=target)
                bpy.data.objects.remove(arm, do_unlink=True)
                raise ValueError('소유자 삭제 뒤 정리 검사')
        self.assertNotIn('RenamedTarget', bpy.data.objects)
        self.assertNotIn(temp.COLLECTION, bpy.data.collections)

    def test_metadata_and_busy(self):
        arm = self.arm
        hidden = arm.data.collections.new('Hidden')
        hidden.assign(arm.data.bones['hand.L'])
        hidden.is_visible = False
        self.assertNotIn('hand.L', {pb.name for pb in context.control_bones(arm)})
        arm.data['rig_studio_info'] = json.dumps({'version': 'test'})
        for name, side in [('hand.L', 'L'), ('hand.R', 'R')]:
            arm.data.bones[name]['rig_studio'] = json.dumps(
                {'type': 'CTRL', 'part': 'arm', 'role': 'hand', 'side': side})
        self.assertEqual(len(context.control_bones(arm)), 2)
        self.assertEqual(mirror.pairs(arm)['hand.L'], 'hand.R')
        self.assertEqual(snapshot.rest_record(arm)['version'], 'test')
        with context.busy():
            with context.busy():
                self.assertTrue(bpy.app.driver_namespace['anim_studio_busy'])
            self.assertTrue(bpy.app.driver_namespace['anim_studio_busy'])
        self.assertNotIn('anim_studio_busy', bpy.app.driver_namespace)

    def test_mirror_bake_custom_properties_and_com(self):
        arm = self.arm
        arm.pose.bones['hand.L']['ik'] = 0.75
        arm.pose.bones['hand.L']['enabled'] = True
        result = bake.bake(arm, ['hand.R'], [1, 2],
                           target_fn=lambda _frame: mirror.mirror_pose(arm, ['hand.L'], 'LEFT_TO_RIGHT'))
        self.assertEqual(result.baked, ['hand.R'])
        bpy.context.scene.frame_set(1)
        self.assertAlmostEqual(arm.pose.bones['hand.R']['ik'], 0.75)
        self.assertTrue(arm.pose.bones['hand.R']['enabled'])
        arm.data['rig_studio_info'] = json.dumps({'version': 'test'})
        arm.data.bones['root']['rig_studio'] = json.dumps(
            {'type': 'CTRL', 'part': 'body', 'side': 'C', 'role': 'com'})
        area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        with bpy.context.temp_override(area=area):
            self.assertEqual(bpy.ops.anim_studio.select_com(), {'FINISHED'})
            self.assertEqual(bpy.context.mode, 'POSE')
            self.assertEqual([pb.name for pb in bpy.context.selected_pose_bones], ['root'])

    def test_operators_and_keymaps(self):
        screen_only = {'toggle_type_visibility', 'toggle_orientation', 'tool_orientation',
                       'view_flip', 'focus_panel', 'open_keymap'}
        for cls in hotkey_ops._CLASSES:
            expected = {'REGISTER'} if cls.bl_idname.split('.')[1] in screen_only else {'REGISTER', 'UNDO'}
            self.assertEqual(cls.bl_options, expected)
            self.assertTrue(cls.bl_description)
        self.assertEqual(len([h for h in bpy.app.handlers.load_post if h is temp._load_post]), 1)
        self.assertTrue(all(item.type == 'NONE' for _km, item in hotkey_ops._addon_keymaps))
        self.assertTrue(all(item.idname.startswith('anim_studio.') for _km, item in hotkey_ops._addon_keymaps))
        area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        with bpy.context.temp_override(area=area):
            self.assertEqual(bpy.ops.anim_studio.key_all_controls(), {'FINISHED'})
            self.assertEqual(bpy.ops.anim_studio.toggle_orientation(), {'FINISHED'})
            self.assertEqual(bpy.ops.anim_studio.easy_resolution(), {'FINISHED'})
            self.assertEqual(bpy.context.scene.render.resolution_x, 640)
            space = area.spaces.active
            before = space.show_object_viewport_mesh
            self.assertEqual(bpy.ops.anim_studio.toggle_type_visibility(kind='MESH'), {'FINISHED'})
            self.assertNotEqual(before, space.show_object_viewport_mesh)
            self.assertEqual(bpy.ops.anim_studio.key_tool_channels(), {'FINISHED'})
            self.assertEqual(bpy.ops.anim_studio.delete_tool_channels(), {'FINISHED'})

    def test_shortcut_conflicts_and_ui_icons(self):
        from anim_studio.ui import icons
        for buttons in hotkey_ops.BUTTONS.values():
            for button in buttons:
                self.assertIn(button.icon, icons._previews)
                self.assertIn(button.icon + '@2x', icons._previews)
        km = bpy.context.window_manager.keyconfigs.user.keymaps.new(name='Window')
        first = km.keymap_items.new('anim_studio.tool_orientation', 'A', 'PRESS', ctrl=True)
        first.properties.tool = 'MOVE'
        second = km.keymap_items.new('transform.translate', 'A', 'PRESS', ctrl=True)
        third = km.keymap_items.new('anim_studio.toggle_orientation', 'A', 'PRESS', ctrl=True)
        builtin = km.keymap_items.new('wm.context_toggle', 'F8', 'PRESS')
        builtin.properties.data_path = 'space_data.show_region_asset_shelf'
        builtin.properties.module = 'math'
        area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        try:
            with bpy.context.temp_override(area=area):
                info = panels.shortcut_info(bpy.context, hotkey_ops.BUTTONS['TRANSFORM'])
                self.assertEqual(info['Move World Local'][0], 'Ctrl A')
                self.assertNotIn(second.name, info['Move World Local'][1])
                self.assertIn(third.name, info['Move World Local'][1])
                self.assertEqual(info['Rotate World Local'], ('', []))
                view_info = panels.shortcut_info(bpy.context, hotkey_ops.BUTTONS['VIEW'])
                self.assertEqual(view_info['Toggle Pose Library'], ('', []))
                builtin.properties.module = ''
                view_info = panels.shortcut_info(bpy.context, hotkey_ops.BUTTONS['VIEW'])
                self.assertEqual(view_info['Toggle Pose Library'][0], 'F8')
                first.active = False
                info = panels.shortcut_info(bpy.context, hotkey_ops.BUTTONS['TRANSFORM'])
                self.assertEqual(info['Move World Local'], ('', []))
        finally:
            km.keymap_items.remove(first)
            km.keymap_items.remove(second)
            km.keymap_items.remove(third)
            km.keymap_items.remove(builtin)

    def test_single_current_frame_preserves_unkeyed_pose(self):
        arm, scene = self.arm, bpy.context.scene
        pb = arm.pose.bones['hand.L']
        cb = fcurve.channelbag(arm)
        path = pb.path_from_id('location')
        fcurve.write_keys(cb, path, 0, [1, 10], [0, 0], pb.name)
        scene.frame_set(5)
        pb.location.x = 2.75
        bpy.context.view_layer.update()
        expected = pb.matrix.copy()
        changes = []
        def frame_changed(*_args):
            changes.append(scene.frame_current_final)
        bpy.app.handlers.frame_change_pre.append(frame_changed)
        try:
            result = bake.bake(arm, [pb.name], [5])
            self.assertEqual(result.baked, [pb.name])
            self.assertEqual(changes, [])
            self.assertLess(matrix_error(expected, pb.matrix), 1e-5)
            self.assertAlmostEqual(cb.fcurves.find(path, index=0).evaluate(5), 2.75)
            pb.location.x = 3.25
            bpy.context.view_layer.update()
            self.assertEqual(bpy.ops.anim_studio.key_all_controls(), {'FINISHED'})
            self.assertEqual(changes, [])
            self.assertAlmostEqual(cb.fcurves.find(path, index=0).evaluate(5), 3.25)
        finally:
            bpy.app.handlers.frame_change_pre.remove(frame_changed)

    def test_parent_unwritten_channels_before_child_conversion(self):
        arm = self.arm
        parent, child = arm.pose.bones['root'], arm.pose.bones['hand.L']
        parent.rotation_mode = 'XYZ'
        original = snapshot.capture(arm, [parent.name, child.name])
        for blocked in ('LOCK', 'DRIVER', 'UNREQUESTED'):
            with self.subTest(blocked=blocked):
                arm.animation_data_clear()
                snapshot.apply(arm, original)
                parent.lock_location[0] = blocked == 'LOCK'
                if blocked == 'DRIVER':
                    arm.driver_add(parent.path_from_id('location'), 0).driver.expression = '0.0'
                parent_matrix = parent.matrix.copy()
                if blocked == 'UNREQUESTED':
                    parent_matrix = parent_matrix @ Euler((0, 0, 0.8)).to_matrix().to_4x4()
                else:
                    parent_matrix.translation.x += 4
                child_matrix = child.matrix.copy()
                child_matrix.translation += Vector((0.7, -0.4, 0.2))
                goal = {parent.name: arm.matrix_world @ parent_matrix,
                        child.name: arm.matrix_world @ child_matrix}
                channels = {'location'} if blocked == 'UNREQUESTED' else fcurve.TRANSFORMS
                bake.bake(arm, list(goal), [1, 2], target_fn=lambda _f: goal, channels=channels)
                for frame in (1, 2):
                    bpy.context.scene.frame_set(frame)
                    self.assertLess(matrix_error(child_matrix, child.matrix), 1e-5)
                    self.assertAlmostEqual(parent.location.x, 0)
                    self.assertAlmostEqual(parent.rotation_euler.z, 0)

    def test_coupled_rotation_locks_and_drivers(self):
        arm, pb = self.arm, self.arm.pose.bones['root']
        cb = fcurve.channelbag(arm)
        for mode, prop in (('QUATERNION', 'rotation_quaternion'), ('AXIS_ANGLE', 'rotation_axis_angle')):
            pb.rotation_mode = mode
            path = pb.path_from_id(prop)
            for blocked in ('LOCK', 'W_LOCK', 'DRIVER'):
                with self.subTest(mode=mode, blocked=blocked):
                    pb.lock_rotation[1] = blocked == 'LOCK'
                    pb.lock_rotation_w = blocked == 'W_LOCK'
                    if blocked == 'DRIVER':
                        arm.driver_add(path, 2).driver.expression = '0.0'
                    for index in range(4):
                        self.assertTrue(fcurve.write_keys(cb, path, index, [1], [0], pb.name))
                        self.assertIsNone(cb.fcurves.find(path, index=index))
                    if blocked == 'DRIVER':
                        arm.driver_remove(path, 2)
                pb.lock_rotation[1] = pb.lock_rotation_w = False

    def test_rotation_continuity_uses_previous_curve_sample(self):
        arm, pb = self.arm, self.arm.pose.bones['hand.L']
        for mode, prop, reference in (
                ('XYZ', 'rotation_euler', (0, 0, 2 * math.pi - 0.1)),
                ('QUATERNION', 'rotation_quaternion', (-1, 0, 0, 0)),
                ('AXIS_ANGLE', 'rotation_axis_angle', (2 * math.pi - 0.1, 0, 0, 1))):
            with self.subTest(mode=mode):
                arm.animation_data_clear()
                pb.matrix_basis = Matrix.Identity(4)
                pb.rotation_mode = mode
                cb = fcurve.channelbag(arm)
                path = pb.path_from_id(prop)
                for index, value in enumerate(reference):
                    fcurve.write_keys(cb, path, index, [0], [value], pb.name)
                target = arm.matrix_world @ pb.bone.matrix_local @ Matrix.Rotation(0.1, 4, 'Z')
                bake.bake(arm, [pb.name], [1, 2], target_fn=lambda _f: {pb.name: target})
                values = [cb.fcurves.find(path, index=i).evaluate(1) for i in range(len(reference))]
                if mode == 'XYZ':
                    self.assertAlmostEqual(values[2], 2 * math.pi + 0.1, places=5)
                else:
                    rotation = (Quaternion(values) if mode == 'QUATERNION'
                                else Quaternion(values[1:], values[0]))
                    previous = (Quaternion(reference) if mode == 'QUATERNION'
                                else Quaternion(reference[1:], reference[0]))
                    self.assertGreater(rotation.dot(previous), 0.99)

    def test_after_measure_callback_and_ik_chain_skip(self):
        arm, pb = self.arm, self.arm.pose.bones['hand.L']
        calls = []
        with temp.session() as session:
            target = session.empty('AfterMeasure', Matrix.Translation((3, 2, 1)))
            session.constraint(pb, 'COPY_TRANSFORMS', target=target, owner_space='WORLD', target_space='WORLD')
            def cleanup():
                calls.append(bpy.context.scene.frame_current)
                self.assertTrue(bpy.app.driver_namespace['anim_studio_busy'])
                session.cleanup()
            result = bake.bake(arm, [pb.name], [1, 2, 3], after_measure=cleanup)
        self.assertEqual(calls, [3])
        self.assertEqual(result.baked, [pb.name])
        self.assertFalse(pb.constraints)
        self.assertNotIn(temp.COLLECTION, bpy.data.collections)
        constraint = arm.pose.bones['tip.L'].constraints.new('IK')
        constraint.chain_count = 2
        bpy.context.view_layer.update()
        self.assertTrue(pb.is_in_ik_chain)
        result = bake.bake(arm, [pb.name], [1, 2])
        self.assertEqual(result.skipped, [(pb.name, 'IK 체인')])

    def test_bulk_key_styles_slotless_and_linked_action(self):
        arm, pb = self.arm, self.arm.pose.bones['hand.L']
        ad = arm.animation_data_create()
        ad.action = bpy.data.actions.new('Slotless')
        self.assertIsNone(fcurve.channelbag(arm, create=False))
        cb = fcurve.channelbag(arm)
        self.assertEqual(ad.action_slot.target_id_type, 'OBJECT')
        path = pb.path_from_id('location')
        fcurve.write_keys(cb, path, 0, [-2, 2, 8, 12], [1, 2, 3, 4], pb.name)
        curve = cb.fcurves.find(path, index=0)
        for point in curve.keyframe_points:
            point.interpolation = 'CONSTANT'
            point.handle_left_type = point.handle_right_type = 'FREE'
        prefs = bpy.context.preferences.edit
        old_interpolation = prefs.keyframe_new_interpolation_type
        try:
            prefs.keyframe_new_interpolation_type = 'LINEAR'
            fcurve.write_keys(cb, path, 0, [1, 5, 9], [9, 8, 7], pb.name)
        finally:
            prefs.keyframe_new_interpolation_type = old_interpolation
        for point in curve.keyframe_points:
            new = 1 <= point.co.x <= 9
            self.assertEqual(point.interpolation, 'LINEAR' if new else 'CONSTANT')
            self.assertEqual(point.handle_left_type, 'AUTO_CLAMPED' if new else 'FREE')
        with tempfile.TemporaryDirectory() as directory:
            file = str(Path(directory) / 'action.blend')
            source_name = ad.action.name
            bpy.data.libraries.write(file, {ad.action})
            with bpy.data.libraries.load(file, link=True) as (_source, target):
                target.actions = [source_name]
            linked = target.actions[0]
            ad.action = linked
            with self.assertRaisesRegex(ValueError, '링크된 액션'):
                fcurve.channelbag(arm)
            ad.action = linked.override_create(remap_local_usages=False)
            self.assertIsNotNone(fcurve.channelbag(arm))

    def test_mirror_repeated_metadata_and_invalid_side(self):
        arm = self.arm
        arm.data['rig_studio_info'] = json.dumps({'version': 'test'})
        for pb in arm.pose.bones:
            pb.bone['rig_studio'] = json.dumps({'type': 'CTRL', 'part': 'same', 'role': 'same',
                                              'side': 'R' if pb.name.endswith('.R') else 'L' if pb.name.endswith('.L') else 'C'})
        self.assertEqual(mirror.pairs(arm)['hand.L'], 'hand.R')
        self.assertEqual(mirror.pairs(arm)['tip.L'], 'tip.L')
        meta = context.bone_meta(arm.pose.bones['hand.R'])
        meta['side'] = 'C'
        arm.data.bones['hand.R']['rig_studio'] = json.dumps(meta)
        self.assertEqual(mirror.pairs(arm)['hand.L'], 'hand.L')
        self.assertEqual(mirror.pairs(arm)['hand.R'], 'hand.R')

    def test_armature_selection_keeps_pose_mode(self):
        first_name = self.arm.name
        second = self.arm.copy()
        second.data = self.arm.data.copy()
        second.name = 'ZSecondRig'
        bpy.context.scene.collection.objects.link(second)
        bpy.ops.object.mode_set(mode='POSE')
        area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        with bpy.context.temp_override(area=area):
            self.assertEqual(bpy.ops.anim_studio.select_next_armature(), {'FINISHED'})
            self.assertEqual(bpy.context.active_object.name, second.name)
            self.assertEqual(bpy.context.mode, 'POSE')
            self.assertEqual(bpy.ops.anim_studio.choose_armature(armature=first_name), {'FINISHED'})
            self.assertEqual(bpy.context.active_object.name, first_name)
            self.assertEqual(bpy.context.mode, 'POSE')

    def test_delete_selected_pose_range_only(self):
        arm = self.arm
        cb = fcurve.channelbag(arm)
        for name in ('hand.L', 'hand.R'):
            pb = arm.pose.bones[name]
            fcurve.write_keys(cb, pb.path_from_id('location'), 0, [-5, 5, 20], [0, 1, 2], name)
        bpy.ops.object.mode_set(mode='POSE')
        for pb in arm.pose.bones:
            pb.select = pb.name == 'hand.L'
        arm.data.bones.active = arm.data.bones['hand.L']
        scene = bpy.context.scene
        scene.use_preview_range = True
        scene.frame_preview_start, scene.frame_preview_end = 1, 10
        area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        with bpy.context.temp_override(area=area):
            self.assertEqual(bpy.ops.anim_studio.delete_keys_outside(), {'FINISHED'})
        left = cb.fcurves.find(arm.pose.bones['hand.L'].path_from_id('location'), index=0)
        right = cb.fcurves.find(arm.pose.bones['hand.R'].path_from_id('location'), index=0)
        self.assertEqual([p.co.x for p in left.keyframe_points], [5])
        self.assertEqual([p.co.x for p in right.keyframe_points], [-5, 5, 20])
        scene.use_preview_range = False


if __name__ == '__main__':
    if not bpy.app.background:
        raise RuntimeError('이 검사는 별도 백그라운드 Blender에서만 실행합니다.')
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Engines)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError('스모크 검사 실패')
