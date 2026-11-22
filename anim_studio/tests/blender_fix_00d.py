# SPDX-License-Identifier: GPL-3.0-or-later
"""00d 순서별 재현 및 회귀 검사. Blender factory startup 전용."""
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy
import anim_studio
from anim_studio.core import bake, context, fcurve, mirror, snapshot
from anim_studio.ops import hotkey_ops
from blender_smoke import Engines


class Fixes(unittest.TestCase):
    setUp = Engines.setUp
    setUpClass = classmethod(lambda cls: anim_studio.register())
    tearDownClass = classmethod(lambda cls: anim_studio.unregister())

    def nla_strip(self, scale):
        arm = self.arm
        pb = arm.pose.bones['root']
        cb = fcurve.channelbag(arm)
        fcurve.write_keys(cb, pb.path_from_id('location'), 0, [1, 11], [0, 2], pb.name)
        ad = arm.animation_data
        action, slot = ad.action, ad.action_slot
        ad.action = None
        track = ad.nla_tracks.new()
        strip = track.strips.new('OffsetScale', 21, action)
        strip.action_slot = slot
        strip.scale = scale
        ad.nla_tracks.active = track
        strip.select = True
        ad.use_tweak_mode = True
        self.assertTrue(ad.use_tweak_mode)
        self.assertAlmostEqual(ad.nla_tweak_strip_time_to_scene(21, invert=True), 1)
        return pb, cb

    def test_02_nla_bake(self):
        pb, cb = self.nla_strip(2)
        frames = list(range(21, 42))
        expected = []
        for frame in frames:
            bpy.context.scene.frame_set(frame)
            expected.append(pb.location.x)
        result = bake.bake(self.arm, [pb.name], frames, channels={'location'})
        curve = cb.fcurves.find(pb.path_from_id('location'), index=0)
        times = [p.co.x for p in curve.keyframe_points]
        print('NLA_BAKE_TIMES', times, result.baked)
        self.assertEqual(times, [1 + i / 2 for i in range(21)])
        errors = []
        for frame, value in zip(frames, expected):
            bpy.context.scene.frame_set(frame)
            errors.append(abs(pb.location.x - value))
        print('NLA_BAKE_ERROR', max(errors))
        self.assertLess(max(errors), 1e-5)

    def test_02_nla_delete(self):
        pb, cb = self.nla_strip(1.5)
        with patch.object(hotkey_ops, '_selected_targets', return_value=[(self.arm, self.arm)]), \
                patch.object(hotkey_ops, 'frame_range', return_value=(21, 36, 'test')):
            hotkey_ops.ANIMSTUDIO_OT_delete_keys_outside.run(None, bpy.context)
        points = [(c.data_path, [p.co.x for p in c.keyframe_points]) for c in cb.fcurves]
        print('NLA_DELETE_REMAINING', points)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0][1], [1, 11])

    def test_03_directional_center(self):
        root = self.arm.pose.bones['root']
        root.location.x = 3
        for mode in ('LEFT_TO_RIGHT', 'RIGHT_TO_LEFT'):
            pose = mirror.mirror_pose(self.arm, ['root', 'hand.L', 'hand.R'], mode)
            snapshot.apply(self.arm, pose)
            print('MIRROR_CENTER', mode, root.location.x, list(pose))
            self.assertNotIn('root', pose)
            self.assertEqual(root.location.x, 3)
        snapshot.apply(self.arm, mirror.mirror_pose(self.arm, ['root'], 'SWAP'))
        self.assertEqual(root.location.x, -3)

    def test_04_property_brackets(self):
        pb = self.arm.pose.bones['root']
        key = 'gain["left\\right"]'
        pb[key] = 0.0
        pose = snapshot.PoseTargets({pb.name: pb.matrix_basis.copy()}, {pb.name: {key: 2.5}}, space='LOCAL')
        result = bake.bake(self.arm, [pb.name], [bpy.context.scene.frame_current], target_fn=lambda _f: pose)
        path = pb.path_from_id() + '["' + bpy.utils.escape_identifier(key) + '"]'
        curve = fcurve.channelbag(self.arm).fcurves.find(path, index=0)
        print('PROPERTY_BRACKETS', repr(key), pb[key], curve.evaluate(bpy.context.scene.frame_current), result.baked)
        self.assertEqual(pb[key], 2.5)
        self.assertEqual(curve.evaluate(bpy.context.scene.frame_current), 2.5)

    def mode_rig(self):
        arm = self.arm
        roles = {'root': 'up_fk', 'tip.L': 'lo_fk', 'hand.L': 'ik', 'hand.R': 'pole'}
        arm.data['rig_studio_info'] = json.dumps({'limbs': [
            {'part': 'arm', 'side': 'L', 'names': {'ik': 'hand.L'}}]})
        for name, role in roles.items():
            arm.pose.bones[name].bone['rig_studio'] = json.dumps(
                {'type': 'CTRL', 'part': 'arm', 'side': 'L', 'role': role})
        ik = arm.pose.bones['hand.L']
        for frame, value in ((1, 0.0), (3, 1.0), (5, 0.0)):
            ik['IK'] = value
            ik.keyframe_insert('["IK"]', frame=frame)
        cb = fcurve.channelbag(arm)
        for curve in cb.fcurves:
            for point in curve.keyframe_points:
                point.interpolation = 'CONSTANT'
        return cb

    def test_05_modes_by_frame(self):
        cb = self.mode_rig()
        arm = self.arm
        root = arm.pose.bones['root']
        fcurve.write_keys(cb, root.path_from_id('location'), 0, [3], [9], root.name)
        result = bake.bake(arm, list(arm.pose.bones.keys()), list(range(1, 7)), channels={'location'})
        for name in arm.pose.bones.keys():
            curve = cb.fcurves.find(arm.pose.bones[name].path_from_id('location'), index=0)
            times = [p.co.x for p in curve.keyframe_points]
            expected = ([1, 2, 3, 5, 6] if name == 'root' else
                        [1, 2, 5, 6] if name == 'tip.L' else [3, 4])
            print('MODE_SPARSE_KEYS', name, times)
            self.assertEqual(times, expected)
        self.assertEqual(cb.fcurves.find(root.path_from_id('location'), index=0).evaluate(3), 9)
        self.assertIn('IK 모드라 FK 컨트롤 2개 제외', result.mode_summary)
        self.assertIn('FK 모드라 IK 컨트롤 2개 제외', result.mode_summary)

    def test_05_key_snapshot_modes(self):
        cb = self.mode_rig()
        arm = self.arm
        bpy.context.scene.frame_set(3)
        root = arm.pose.bones['root']
        root.location.x = 7
        pose = snapshot.capture(arm, list(arm.pose.bones.keys()))
        root.location.x = 8
        skipped = snapshot.apply(arm, pose)
        self.assertEqual(root.location.x, 8)
        self.assertEqual(len(skipped), 2)
        result = hotkey_ops._key_targets(bpy.context, [(arm, pb) for pb in arm.pose.bones], {'location'})
        self.assertIn('IK 모드라 FK 컨트롤 2개 제외', result)
        self.assertIsNone(cb.fcurves.find(root.path_from_id('location'), index=0))
        print('MODE_KEY_STATUS', result)

    def test_05_nla_mode_evaluate(self):
        self.mode_rig()
        _pb, cb = self.nla_strip(2)
        names, skipped = context.active_mode_controls(self.arm, list(self.arm.pose.bones.keys()), 25)
        print('NLA_MODE', names, skipped)
        self.assertEqual(set(names), {'hand.L', 'hand.R'})

    def test_05_nla_rotation_reference(self):
        pb, cb = self.nla_strip(2)
        pb.rotation_mode = 'XYZ'
        path = pb.path_from_id('rotation_euler')
        curve = cb.fcurves.new(path, index=0)
        curve.keyframe_points.insert(1, 7)
        curve.keyframe_points.insert(11, 20)
        reference = bake._rotation_reference(pb, cb, 21)
        print('NLA_ROTATION_REFERENCE', reference.x)
        self.assertAlmostEqual(reference.x, 7)

    def test_01_outside_preserves_pose(self):
        arm = self.arm
        a, b = arm.pose.bones['root'], arm.pose.bones['hand.L']
        cb = fcurve.channelbag(arm)
        for route in ('operator', 'core'):
            for pb in (a, b):
                fcurve.write_keys(cb, pb.path_from_id('location'), 0, [-5, 5, 20], [0, 0, 0], pb.name)
            bpy.context.scene.frame_set(5)
            a.location.x, b.location.x = 3, 4
            bpy.context.view_layer.update()
            events = []
            def changed(*_args):
                events.append(1)
            bpy.app.handlers.frame_change_pre.append(changed)
            try:
                if route == 'operator':
                    with patch.object(hotkey_ops, '_selected_targets', return_value=[(arm, a)]), \
                            patch.object(hotkey_ops, 'frame_range', return_value=(1, 10, 'test')):
                        bpy.ops.object.mode_set(mode='POSE')
                        hotkey_ops.ANIMSTUDIO_OT_delete_keys_outside.run(None, bpy.context)
                else:
                    fcurve.delete_keys_outside(cb, 1, 10, data_paths={a.path_from_id('location')})
                bpy.context.view_layer.update()
            finally:
                bpy.app.handlers.frame_change_pre.remove(changed)
            print('OUTSIDE_POSE', route, a.location.x, b.location.x, 'frame_set', len(events))
            self.assertEqual((a.location.x, b.location.x), (3, 4))
            self.assertEqual(events, [])
            self.assertEqual([p.co.x for p in cb.fcurves.find(a.path_from_id('location'), index=0).keyframe_points], [5])
            self.assertEqual(len(cb.fcurves.find(b.path_from_id('location'), index=0).keyframe_points), 3)


if __name__ == '__main__':
    names = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    suite = (unittest.defaultTestLoader.loadTestsFromNames(names, Fixes) if names else
             unittest.defaultTestLoader.loadTestsFromTestCase(Fixes))
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
