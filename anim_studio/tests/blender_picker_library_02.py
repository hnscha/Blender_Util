# SPDX-License-Identifier: GPL-3.0-or-later
"""새 factory 프로세스에서 피커/애셋 수치 검증. 실행 인자는 보고서 참조."""
import json
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy
from mathutils import Matrix
import anim_studio
from anim_studio.core import picker, pose_library as library, pose_tools as pose, fcurve, temp
from blender_smoke import Engines, matrix_error


def check_layout(buttons):
    assert len({b['bone'] for b in buttons}) == len(buttons)
    for i, a in enumerate(buttons):
        assert 0 <= a['x'] < a['x'] + a['w'] <= 1
        assert 0 <= a['y'] < a['y'] + a['h'] <= 1
        for b in buttons[i + 1:]:
            assert not (min(a['x'] + a['w'], b['x'] + b['w']) > max(a['x'], b['x']) and
                        min(a['y'] + a['h'], b['y'] + b['h']) > max(a['y'], b['y'])), (a, b)


class PickerLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        anim_studio.register()

    @classmethod
    def tearDownClass(cls):
        anim_studio.unregister()
        assert temp._load_pre not in bpy.app.handlers.load_pre
        assert temp._load_post not in bpy.app.handlers.load_post

    def setUp(self):
        Engines.setUp(self)
        for action in list(bpy.data.actions):
            bpy.data.actions.remove(action, do_unlink=True)
        bpy.ops.object.mode_set(mode='POSE')
        picker.select(self.arm, ['hand.L', 'hand.R'])
        self.area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        self.region = next(r for r in self.area.regions if r.type == 'WINDOW')

    def save(self):
        with bpy.context.temp_override(area=self.area, region=self.region):
            return library.save(bpy.context, 'Test Pose', thumbnail=False)

    def test_layout(self):
        result = picker.rebuild(self.arm)
        check_layout(result)
        self.assertEqual(result, json.loads(self.arm.data[picker.PROPERTY]))
        self.assertEqual({b['bone'] for b in result}, set(self.arm.pose.bones.keys()))
        self.assertEqual(next(b['zone'] for b in result if b['bone'] == 'tip.L'), 'Other')
        self.assertEqual(picker.layout(self.arm), result)

    def test_name_rules(self):
        bpy.ops.object.mode_set(mode='EDIT')
        for name in ('LeftArm', 'RightArm', 'forearm_L', 'forearm_R', 'thigh.L', 'shin.R',
                     'head', 'neck', 'spine', 'chest', 'hips', 'toe_L', 'thumb.01.L',
                     'index_02_R', 'middleLeft', 'ringRight', 'pinky.L', 'unknown'):
            b = self.arm.data.edit_bones.new(name)
            b.head, b.tail = (0, 0, 0), (0, 1, 0)
        bpy.ops.object.mode_set(mode='POSE')
        result = picker.generate(self.arm)
        check_layout(result)
        zones = {b['bone']: b['zone'] for b in result}
        self.assertEqual(zones['LeftArm'], 'Arm L')
        self.assertEqual(zones['forearm_R'], 'Arm R')
        self.assertEqual(zones['index_02_R'], 'Fingers R')
        self.assertEqual(zones['hips'], 'COM')

    def test_review_crowded_and_word_rules(self):
        # 리뷰 1: Other가 많아도 배치가 유효하고 Rebuild 가 복구된다. 단어 경계 오분류 없음.
        bpy.ops.object.mode_set(mode='EDIT')
        names = [f'face_{i:03d}' for i in range(130)] + [f'back_{i:02d}' for i in range(30)] + [
            'earring.L', 'spring_R', 'skirt_back_01', 'hair_back', 'LeftHandIndex1', 'c_toes.l']
        for name in names:
            b = self.arm.data.edit_bones.new(name)
            b.head, b.tail = (0, 0, 0), (0, 1, 0)
        bpy.ops.object.mode_set(mode='POSE')
        result = picker.rebuild(self.arm)
        check_layout(result)
        self.assertTrue(picker.valid(result))
        self.assertEqual(picker.layout(self.arm), result)
        self.assertTrue(all(b['h'] * picker.TEMPLATE[3] >= 1.4 and b['w'] * picker.TEMPLATE[2] >= 1.4 for b in result))
        zones = {b['bone']: b['zone'] for b in result}
        for name in ('earring.L', 'spring_R'):
            self.assertNotIn(zones.get(name, 'Other'), {'Fingers L', 'Fingers R'})
        for name in ('skirt_back_01', 'hair_back'):
            self.assertNotEqual(zones.get(name, 'Other'), 'Spine')
        self.assertEqual(zones['LeftHandIndex1'], 'Fingers L')
        self.assertEqual(zones['c_toes.l'], 'Leg L')

    def test_review_asset_selection_by_name(self):
        # 리뷰 2: 이름이 앞서는 액션이 생겨도 선택 애셋이 바뀌지 않는다.
        from anim_studio.ops import library_ops
        with bpy.context.temp_override(area=self.area, region=self.region):
            library.save(bpy.context, 'PoseA', thumbnail=False)
            library.save(bpy.context, 'PoseB', thumbnail=False)
        wm = bpy.context.window_manager
        wm.anim_studio_asset_index = bpy.data.actions.find('PoseB')
        self.assertEqual(wm.anim_studio_asset_name, 'PoseB')
        bpy.data.actions.new('Aaa')
        self.assertEqual(library_ops.selected_asset(bpy.context).name, 'PoseB')
        self.assertEqual(wm.anim_studio_asset_index, bpy.data.actions.find('PoseB'))

    def test_selection(self):
        arm = self.arm
        self.assertEqual(picker.select(arm, ['hand.L']), {'hand.L'})
        self.assertEqual(arm.data.bones.active.name, 'hand.L')
        self.assertEqual(picker.select(arm, ['hand.R'], 'TOGGLE'), {'hand.L', 'hand.R'})
        self.assertEqual(picker.select(arm, ['hand.L'], 'TOGGLE'), {'hand.R'})
        self.assertEqual(picker.select(arm, ['hand.R'], 'SUB'), set())
        buttons = picker.generate(arm)
        self.assertEqual(set(picker.chip_names(arm, buttons, 'Arm L')), {'hand.L'})
        picker.select(arm, ['hand.L'])
        self.assertEqual(picker.chip_names(arm, buttons, 'Mirror'), ['hand.R'])
        # Spine 칩은 척추 컨트롤만(머리·COM 제외).
        self.assertEqual(picker.chip_names(arm, buttons, 'Spine'), [])
        self.assertNotIn('root', picker.chip_names(arm, buttons, 'Spine'))
        self.assertEqual(set(picker.box_names(buttons, (0, 0, 1, 1))), set(arm.pose.bones.keys()))
        arm.pose.bones['hand.R'].hide = True
        self.assertEqual(picker.select(arm, ['hand.R', 'missing']), set())
        arm.pose.bones['hand.R'].hide = False
        arm.pose.bones['hand.R'].bone.hide_select = True
        self.assertNotIn('hand.R', picker.enabled(arm))

    def test_asset_roundtrip_mirror_blend(self):
        arm = self.arm
        left = arm.pose.bones['hand.L']
        left.location = (.4, -.2, .8)
        left.rotation_euler = (.2, -.3, .1)
        left.scale = (1.2, .9, 1.1)
        left.bbone_curveinx = .23
        bpy.context.view_layer.update()
        original = pose.current(arm, ['hand.L', 'hand.R'])
        frame = bpy.context.scene.frame_current_final
        action = self.save()
        self.assertTrue(action.asset_data)
        self.assertIsNone(action.preview)
        result, extras = library.read(arm, action)
        self.assertEqual(set(result), {'hand.L', 'hand.R'})
        left.matrix_basis = Matrix.Identity(4)
        left.bbone_curveinx = 0
        bpy.context.view_layer.update()
        start = pose.current(arm, result)
        s = library.BlendSession(arm, result, extras)
        s.apply(.5)
        expected = pose.blend(start, original, .5)
        self.assertLess(matrix_error(left.matrix_basis, expected['hand.L']), 1e-4)
        self.assertAlmostEqual(left.bbone_curveinx, .115, places=5)
        s.apply(1.5)
        self.assertLess(matrix_error(left.matrix_basis, pose.blend(start, original, 1.5)['hand.L']), 1e-4)
        s.restore()
        self.assertLess(matrix_error(left.matrix_basis, start['hand.L']), 1e-6)
        library.apply(arm, result, extras)
        self.assertLess(matrix_error(left.matrix_basis, original['hand.L']), 1e-4)
        self.assertAlmostEqual(left.bbone_curveinx, .23, places=5)
        expected = pose.mirror_saved(arm, result)
        mirrored, extra = library.target(arm, action, flipped=True)
        library.apply(arm, mirrored, extra)
        for n in expected:
            self.assertLess(matrix_error(arm.pose.bones[n].matrix_basis, expected[n]), 1e-4)
        self.assertEqual(frame, bpy.context.scene.frame_current_final)
        self.assertIsNone(arm.animation_data.action if arm.animation_data else None)
        self.assertNotIn('anim_studio_busy', bpy.app.driver_namespace)
        print('ASSET02_MAX_ERROR', max(matrix_error(arm.pose.bones[n].matrix_basis, expected[n]) for n in expected), flush=True)

    def test_selected_locks_key(self):
        arm = self.arm
        arm.pose.bones['hand.L'].location.x = .7
        action = self.save()
        picker.select(arm, ['hand.L'])
        result, extras = library.target(arm, action, selected_only=True)
        self.assertEqual(set(result), {'hand.L'})
        pb = arm.pose.bones['hand.L']
        pb.location.x = .2
        pb.lock_location[0] = True
        library.apply(arm, result, extras, key=True)
        self.assertAlmostEqual(pb.location.x, .2)
        cb = fcurve.channelbag(arm, False)
        self.assertIsNone(cb.fcurves.find(pb.path_from_id('location'), index=0))
        self.assertFalse(any('hand.R' in fc.data_path for fc in cb.fcurves))

    def test_escaped_names_rotation_and_custom(self):
        arm = self.arm
        pb = arm.pose.bones['hand.L']
        pb.name = 'control["test"].L'
        pb.rotation_mode = 'ZYX'
        pb.rotation_euler = (.7, .2, -.4)
        pb['amount[0]'] = .35
        pb.keyframe_insert('["amount[0]"]')
        action = self.save()
        expected = pb.matrix_basis.copy()
        pb.rotation_mode = 'QUATERNION'
        pb.rotation_quaternion = (1, 0, 0, 0)
        result, extras = library.read(arm, action)
        library.apply(arm, result, extras, key=True)
        self.assertLess(matrix_error(pb.matrix_basis, expected), 1e-4)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PickerLibrary)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('PICKER_LIBRARY02_OK', flush=True)
