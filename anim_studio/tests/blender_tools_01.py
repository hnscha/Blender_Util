# SPDX-License-Identifier: GPL-3.0-or-later
"""01 실제 오퍼레이터·행렬 회귀. 별도 factory Blender에서 실행."""
import json
import math
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy
from mathutils import Matrix, Quaternion
import anim_studio
from anim_studio.core import fcurve, mirror, pose_tools as pose, space, temp
from blender_smoke import Engines, matrix_error


class Tools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        anim_studio.register()

    @classmethod
    def tearDownClass(cls):
        anim_studio.unregister()

    def setUp(self):
        Engines.setUp(self)
        bpy.ops.object.mode_set(mode='POSE')
        self.select('hand.L', 'hand.R')
        self.area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        self.region = next(r for r in self.area.regions if r.type == 'WINDOW')
        self.scene, self.wm = bpy.context.scene, bpy.context.window_manager
        self.scene.use_preview_range = False
        self.scene.frame_start, self.scene.frame_end = 0, 20
        self.scene.frame_set(5)
        self.wm.anim_studio_history.clear()
        self.wm.anim_studio_align = ''
        self.wm.anim_studio_paste_space = 'LOCAL'
        self.wm.anim_studio_mirror_mode = 'SWAP'
        self.wm.anim_studio_km_world = False
        self.wm.anim_studio_km_rot = True
        self.wm.anim_studio_align_pos = self.wm.anim_studio_align_rot = True
        self.wm.anim_studio_align_scale = False
        self.wm.anim_studio_follow_pos = self.wm.anim_studio_follow_rot = True

    def tearDown(self):
        temp.cleanup_all()

    def select(self, *names):
        for pb in self.arm.pose.bones:
            pb.select = pb.name in names
        self.arm.data.bones.active = self.arm.data.bones[names[-1]] if names else None

    def op(self, name, **kwargs):
        with bpy.context.temp_override(area=self.area, region=self.region):
            result = getattr(bpy.ops.anim_studio, name)(**kwargs)
        self.assertEqual(result, {'FINISHED'})
        return result

    def close(self, actual, expected, label):
        error = matrix_error(actual, expected)
        print('TOOLS01', label, error, flush=True)
        self.assertLessEqual(error, 1e-4)

    def test_copy_paste_local_world_and_history(self):
        pb = self.arm.pose.bones['hand.L']
        self.select('hand.L')
        pb.location = (1, 2, 3)
        pb.rotation_euler = (.2, -.3, .1)
        bpy.context.view_layer.update()
        local, world = pb.matrix_basis.copy(), self.arm.matrix_world @ pb.matrix
        self.op('pose_copy')
        self.scene.frame_set(7)
        self.arm.pose.bones['root'].location.x = 4
        pb.location = (0, 0, 0)
        other = self.arm.pose.bones['hand.R']
        other.location.z = 7
        count = []
        handler = lambda *_: count.append(1)
        bpy.app.handlers.frame_change_post.append(handler)
        try:
            self.op('pose_paste')
            self.close(pb.matrix_basis, local, 'paste_local')
            self.assertAlmostEqual(other.location.z, 7)
            self.wm.anim_studio_paste_space = 'WORLD'
            self.op('pose_paste')
            self.close(self.arm.matrix_world @ pb.matrix, world, 'paste_world')
            self.assertEqual(count, [])
        finally:
            bpy.app.handlers.frame_change_post.remove(handler)
        for _ in range(12):
            self.op('pose_copy')
        self.assertEqual(len(self.wm.anim_studio_history), 10)
        self.op('pose_history_delete', index=0)
        self.assertEqual(len(self.wm.anim_studio_history), 9)
        self.op('pose_history_delete', clear_all=True)
        self.assertEqual(len(self.wm.anim_studio_history), 0)

    def test_mirror_opposite_and_twice(self):
        left, right = self.arm.pose.bones['hand.L'], self.arm.pose.bones['hand.R']
        left.location = (.5, .2, .1)
        left.rotation_euler = (.3, -.4, .2)
        right.rotation_euler = (-.2, .3, .1)
        bpy.context.view_layer.update()
        self.select('hand.L')
        expected = mirror.mirror_pose(self.arm, ['hand.L'])['hand.R']
        self.op('pose_copy')
        self.op('pose_paste_opposite')
        self.close(right.matrix_basis, expected, 'opposite')
        self.select('hand.L', 'hand.R')
        before = {pb.name: pb.matrix_basis.copy() for pb in (left, right)}
        self.op('mirror_pose')
        self.op('mirror_pose')
        for pb in (left, right):
            self.close(pb.matrix_basis, before[pb.name], 'mirror_twice_' + pb.name)

    def test_custom_properties_existing_keys_and_locks(self):
        pb = self.arm.pose.bones['hand.L']
        self.select(pb.name)
        pb['gain[unit]'] = 1.0
        pb.keyframe_insert('["gain[unit]"]', frame=0)
        pb['unkeyed'] = 2.0
        pb.location = (2, 3, 4)
        self.op('pose_copy')
        pb.location = (7, 8, 9)
        pb.lock_location[0] = True
        self.op('pose_paste')
        self.assertAlmostEqual(pb.location.x, 7)
        self.assertAlmostEqual(pb.location.y, 3)
        cb = fcurve.channelbag(self.arm)
        self.assertIsNone(cb.fcurves.find(pb.path_from_id() + '["unkeyed"]', index=0))
        self.assertAlmostEqual(cb.fcurves.find(pb.path_from_id() + '["gain[unit]"]', index=0).evaluate(5), 1)

    def test_tween_neighbors_and_overshoot(self):
        pb = self.arm.pose.bones['hand.L']
        self.select(pb.name)
        pb.rotation_mode = 'QUATERNION'
        for frame, x, angle in ((0, 0, 0), (10, 10, math.pi / 2)):
            pb.location.x = x
            pb.rotation_quaternion = Quaternion((0, 0, 1), angle)
            pb.keyframe_insert('location', frame=frame)
            pb.keyframe_insert('rotation_quaternion', frame=frame)
        self.scene.frame_set(5)
        calls = []
        handler = lambda *_: calls.append(1)
        bpy.app.handlers.frame_change_post.append(handler)
        try:
            self.op('tween_set', target='NEIGHBORS', factor=.5)
            self.assertAlmostEqual(pb.location.x, 5, places=5)
            self.close(pb.rotation_quaternion.to_matrix().to_4x4(),
                       Quaternion((0, 0, 1), math.pi / 4).to_matrix().to_4x4(), 'tween_slerp')
            self.op('tween_set', target='NEIGHBORS', factor=1.5)
            self.assertAlmostEqual(pb.location.x, 15, places=5)
            self.assertEqual(calls, [])
        finally:
            bpy.app.handlers.frame_change_post.remove(handler)

    def test_key_match_local_world(self):
        root, hand = self.arm.pose.bones['root'], self.arm.pose.bones['hand.L']
        self.select(hand.name)
        for frame, x in ((0, 1), (10, 4)):
            root.location.x = x * 2
            root.keyframe_insert('location', frame=frame)
            hand.location.y = x
            hand.keyframe_insert('location', frame=frame)
        self.scene.frame_set(0)
        expected = self.arm.matrix_world @ hand.matrix
        self.scene.frame_set(5)
        self.op('key_match', direction='PREV')
        self.assertAlmostEqual(hand.location.y, 1)
        self.wm.anim_studio_km_world = True
        self.op('key_match', direction='PREV')
        self.close(self.arm.matrix_world @ hand.matrix, expected, 'key_match_world')
        self.op('key_match', direction='NEXT')
        current = (self.arm.matrix_world @ hand.matrix).copy()
        self.scene.frame_set(10)
        self.close(current, self.arm.matrix_world @ hand.matrix, 'key_match_next_world')

    def test_pivot_apply_delete_bake(self):
        pb = self.arm.pose.bones['hand.L']
        self.select(pb.name)
        original = pb.matrix_basis.copy()
        self.op('temp_pivot_create')
        pivot = space.pivot_state(bpy.context)[0]
        pivot.rotation_euler.z = .4
        pivot.location.x += 2
        bpy.context.view_layer.update()
        expected = self.arm.matrix_world @ pb.matrix
        self.op('temp_pivot_apply')
        self.close(self.arm.matrix_world @ pb.matrix, expected, 'pivot_apply')
        self.assertIsNone(space.pivot_state(bpy.context))
        self.assertEqual(len(pb.constraints), 0)
        before_delete = pb.matrix_basis.copy()
        self.op('temp_pivot_create')
        space.pivot_state(bpy.context)[0].location.y += 4
        bpy.context.view_layer.update()
        self.op('temp_pivot_delete')
        self.close(pb.matrix_basis, before_delete, 'pivot_delete')
        self.scene.frame_start, self.scene.frame_end = 1, 4
        self.op('temp_pivot_create')
        pivot = space.pivot_state(bpy.context)[0]
        for frame, x in ((1, 0), (4, 2)):
            pivot.location.x = x
            pivot.keyframe_insert('location', frame=frame)
        measured = pose.measure(self.arm, [pb.name], range(1, 5))
        self.op('temp_pivot_bake')
        for frame in range(1, 5):
            self.scene.frame_set(frame)
            self.close(self.arm.matrix_world @ pb.matrix, measured[frame][pb.name], 'pivot_bake')
        self.assertEqual(temp.cleanup_all(), 0)

    def test_lock_and_align_range(self):
        root, left, right = (self.arm.pose.bones[n] for n in ('root', 'hand.L', 'hand.R'))
        for frame, x in ((0, 0), (4, 2)):
            root.location.x = x
            root.keyframe_insert('location', frame=frame)
            right.location.z = x
            right.keyframe_insert('location', frame=frame)
        self.scene.frame_set(2)
        self.scene.frame_start, self.scene.frame_end = 0, 4
        self.select(left.name)
        fixed = self.arm.matrix_world @ left.matrix
        self.op('world_lock_range')
        for frame in range(5):
            self.scene.frame_set(frame)
            self.close(self.arm.matrix_world @ left.matrix, fixed, 'world_lock')
        self.select(left.name, right.name)
        self.op('align_set')
        self.op('align_now')
        self.close(left.matrix, right.matrix, 'align_now')
        self.op('align_range')
        for frame in range(5):
            self.scene.frame_set(frame)
            self.close(left.matrix, right.matrix, 'align_range')

    def test_range_paste_mirror_and_fractional_keys(self):
        pb = self.arm.pose.bones['hand.L']
        self.select(pb.name)
        for frame, x in ((0, 1), (2.5, 2), (10, 3)):
            pb.location.x = x
            pb.keyframe_insert('location', frame=frame)
        self.scene.frame_set(5)
        pb.location.x = 7
        self.op('pose_copy')
        self.op('pose_paste_range')
        self.assertEqual(pose.key_frames(self.arm, [pb.name]), [0, 2.5, 10])
        for frame in (0, 2.5, 10):
            self.assertAlmostEqual(pose.evaluate_local(self.arm, [pb.name], frame)[pb.name].translation.x, 7)
        self.op('mirror_range')
        right = self.arm.pose.bones['hand.R']
        for frame in (0, 2.5, 10):
            self.assertAlmostEqual(pose.evaluate_local(self.arm, [right.name], frame)[right.name].translation.x, -7)

    def test_loop_and_single_frame(self):
        left = self.arm.pose.bones['hand.L']
        self.select('hand.L', 'hand.R')
        for frame, x in ((0, 1), (5, 2), (10, 3), (20, 1)):
            left.location.x = x
            left.keyframe_insert('location', frame=frame)
        original = pose.measure(self.arm, ['hand.L', 'hand.R'], [0, 5], 'LOCAL')
        self.op('mirror_loop', half='FIRST')
        for source, target in ((0, 10), (5, 15)):
            expected = pose.mirror_saved(self.arm, original[source])
            actual = pose.evaluate_local(self.arm, list(expected), target)
            for n in expected:
                self.close(actual[n], expected[n], 'loop_first')
        self.scene.frame_set(5)
        left.location.x = 6
        self.op('mirror_loop_single')
        self.assertAlmostEqual(pose.evaluate_local(self.arm, ['hand.R'], 15)['hand.R'].translation.x, -6)
        self.scene.frame_set(20)
        left.location.x = 8
        self.op('mirror_loop_single')
        self.assertAlmostEqual(pose.evaluate_local(self.arm, ['hand.R'], 10)['hand.R'].translation.x, -8)
        self.op('mirror_loop', half='SECOND')

    def test_nla_native_range_and_key_match(self):
        from blender_fix_00d import Fixes
        pb, cb = Fixes.nla_strip(self, 2)
        self.select(pb.name)
        self.scene.frame_start, self.scene.frame_end = 21, 41
        self.scene.frame_set(31)
        self.assertEqual(pose.key_frames(self.arm, [pb.name]), [21, 41])
        pb.location.x = 3
        self.op('pose_copy')
        strip = self.arm.animation_data.nla_tracks[0].strips[0]
        bounds = (strip.frame_start, strip.frame_end, strip.action_frame_start, strip.action_frame_end)
        self.op('pose_paste_range')
        self.assertEqual((strip.frame_start, strip.frame_end, strip.action_frame_start, strip.action_frame_end), bounds)
        curve = cb.fcurves.find(pb.path_from_id('location'), index=0)
        self.assertEqual([p.co.x for p in curve.keyframe_points], [1, 11])
        self.assertEqual([p.co.y for p in curve.keyframe_points], [3, 3])
        self.op('key_match', direction='PREV')
        self.assertEqual([p.co.x for p in curve.keyframe_points], [1, 6, 11])

    def test_loop_root_progress_with_animated_armature(self):
        self.arm.data['rig_studio_info'] = json.dumps({'limbs': []})
        for pb in self.arm.pose.bones:
            pb.bone['rig_studio'] = json.dumps({'type': 'CTRL', 'part': 'torso' if pb.name == 'root' else 'arm',
                'role': 'root' if pb.name == 'root' else 'test',
                'side': 'L' if pb.name.endswith('.L') else 'R' if pb.name.endswith('.R') else 'C'})
        root = self.arm.pose.bones['root']
        self.select(root.name)
        for frame in (0, 5, 10, 20):
            root.location.y = frame
            root.keyframe_insert('location', frame=frame)
            self.arm.location.y = frame * 2
            self.arm.keyframe_insert('location', frame=frame)
        self.scene.frame_set(5)
        self.op('mirror_loop', half='FIRST')
        for frame in (10, 15, 20):
            self.scene.frame_set(frame)
            self.assertAlmostEqual(root.matrix.translation.y, frame, places=5)
        self.scene.frame_set(5)
        root.location.y = 8
        self.op('mirror_loop_single')
        self.scene.frame_set(15)
        self.assertAlmostEqual(root.matrix.translation.y, 18, places=5)

    def test_native_range_preserves_modifiers_and_preferences(self):
        pb = self.arm.pose.bones['hand.L']
        path = pb.path_from_id('location')
        cb = fcurve.channelbag(self.arm)
        fcurve.write_keys(cb, path, 0, [0, 10], [1, 2], pb.name)
        curve = cb.fcurves.find(path, index=0)
        modifier = curve.modifiers.new('CYCLES')
        modifier.mode_before = 'REPEAT_OFFSET'
        curve.extrapolation = 'LINEAR'
        preferences = bpy.context.preferences.edit
        old = preferences.keyframe_new_handle_type
        preferences.keyframe_new_handle_type = 'VECTOR'
        try:
            fcurve.write_keys(cb, path, 0, [0, 10], [3, 4], pb.name,
                              time_owner=self.arm, native_owner=self.arm)
            self.assertEqual(preferences.keyframe_new_handle_type, 'VECTOR')
            self.assertEqual(len(curve.modifiers), 1)
            self.assertEqual(curve.modifiers[0].mode_before, 'REPEAT_OFFSET')
            self.assertEqual(curve.extrapolation, 'LINEAR')
            self.assertEqual([p.co.x for p in curve.keyframe_points], [0, 10])
            self.assertTrue(all(p.handle_left_type == 'AUTO_CLAMPED' for p in curve.keyframe_points))
        finally:
            preferences.keyframe_new_handle_type = old

    def test_pivot_locked_preflight_and_delete(self):
        from anim_studio.core.errors import StudioError
        self.select('hand.L')
        with bpy.context.temp_override(area=self.area, region=self.region):
            space.create_pivot(bpy.context)
        pb = self.arm.pose.bones['hand.L']
        pb.lock_location = pb.lock_rotation = pb.lock_scale = (True, True, True)
        pb.lock_rotation_w = True
        with self.assertRaises(StudioError):
            space.apply_pivot(bpy.context, [5])
        self.assertIsNotNone(space.pivot_state(bpy.context))
        self.op('temp_pivot_delete')
        self.assertIsNone(space.pivot_state(bpy.context))

    def test_paste_range_uses_all_selected_key_times(self):
        left, right = self.arm.pose.bones['hand.L'], self.arm.pose.bones['hand.R']
        self.select(left.name)
        left.location.x = 2
        self.op('pose_copy')
        left.keyframe_insert('location', frame=0)
        right.keyframe_insert('location', frame=3)
        self.select(left.name, right.name)
        self.op('pose_paste_range')
        self.assertEqual(pose.key_frames(self.arm, [left.name]), [0, 3])

    def test_align_to_active_object(self):
        self.select('hand.L')
        target = bpy.data.objects.new('AlignmentTarget', None)
        self.scene.collection.objects.link(target)
        target.matrix_world = Matrix.Translation((3, 4, 5))
        target.select_set(True)
        bpy.context.view_layer.objects.active = target
        bpy.context.view_layer.update()
        self.op('align_set')
        state = json.loads(self.wm.anim_studio_align)
        self.assertEqual(state['target_object'], target.name)
        self.assertEqual(state['target_bone'], '')
        self.op('align_now')
        self.close(self.arm.matrix_world @ self.arm.pose.bones['hand.L'].matrix,
                   target.matrix_world, 'align_object')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tools))
    if not result.wasSuccessful():
        raise RuntimeError('01 도구 검사 실패')
