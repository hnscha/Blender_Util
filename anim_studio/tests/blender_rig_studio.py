# SPDX-License-Identifier: GPL-3.0-or-later
"""실제 인접 Rig Studio 소스를 읽어 새 백그라운드 씬에서 미러를 검증한다."""
import random
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'Blender_Rig'))
from rig_studio import build
from anim_studio.core import context, mirror, snapshot


def matrix_error(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))


class RigStudioMirror(unittest.TestCase):
    def test_real_rig_fk_swap_twice(self):
        self.check_roundtrip(False)

    def test_real_rig_all_controls_swap_twice(self):
        self.check_roundtrip(True)

    def check_roundtrip(self, all_controls):
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        arm = build.build()
        # 00d: FK 포즈 검사는 실제 모드도 FK로 지정한다(빌드 기본값은 IK).
        for limb in json.loads(arm.data['rig_studio_info'])['limbs']:
            arm.pose.bones[limb['names']['ik']]['IK'] = 0.0
        bpy.context.view_layer.update()
        controls = context.control_bones(arm)
        names = [pb.name for pb in controls]
        # 애니메이터 FK 포즈: IK 추종 컨트롤·밴딩 보조 본은 리그가 평가한다.
        fk_roles = {'root', 'com', 'pelvis', 'spine_fk', 'neck', 'head',
                    'clavicle', 'up_fk', 'lo_fk', 'end_fk', 'finger', 'toe'}
        selected = [pb.name for pb in controls if context.bone_meta(pb)['role'] in fk_roles]
        fk = list(selected)
        if all_controls:
            selected = names
        metadata = [context.bone_meta(pb) for pb in arm.pose.bones]
        counts = Counter(tuple(meta.get(k) for k in ('type', 'part', 'side', 'role'))
                         for meta in metadata if meta)
        self.assertGreater(max(counts.values()), 1)
        mapping = mirror.pairs(arm)
        self.assertEqual(mapping['spine_fk_02'], 'spine_fk_02')
        self.assertEqual(mapping['MCH-upper_arm_ik.L'], 'MCH-upper_arm_ik.R')
        self.assertEqual(mapping['MCH-forearm_ik.L'], 'MCH-forearm_ik.R')
        for name, partner in mapping.items():
            self.assertEqual(mapping[partner], name)
            meta = context.bone_meta(arm.pose.bones[name])
            if meta and meta['side'] == 'C':
                self.assertEqual(partner, name)

        rng = random.Random(240522)
        for name in fk:
            pb = arm.pose.bones[name]
            for index in range(3):
                if not pb.lock_rotation[index]:
                    pb.rotation_euler[index] = rng.uniform(-0.22, 0.22)
        arm.pose.bones['root'].location.x = 0.35
        arm.pose.bones['hand_fk.L']['mirror_test_value'] = 0.25
        arm.pose.bones['hand_fk.R']['mirror_test_value'] = 0.75
        for name in fk:
            pb = arm.pose.bones[name]
            for channel in ('location', 'rotation_euler', 'scale'):
                pb.keyframe_insert(channel, frame=37)
        bpy.context.scene.frame_set(37)
        bpy.context.view_layer.update()
        before = {name: (arm.matrix_world @ arm.pose.bones[name].matrix).copy() for name in names}
        props = {name: snapshot.numeric_properties(arm.pose.bones[name]) for name in names}
        defs = {pb.name: (arm.matrix_world @ pb.matrix).copy() for pb in arm.pose.bones
                if context.bone_meta(pb).get('type') == 'DEF'}
        first = mirror.mirror_pose(arm, selected, mode='SWAP')
        snapshot.apply(arm, first)
        errors = sorted(((matrix_error(matrix, arm.pose.bones[name].matrix_basis if first.space == 'LOCAL'
                                       else arm.matrix_world @ arm.pose.bones[name].matrix), name)
                         for name, matrix in first.items()), reverse=True)
        print('RIG_FIRST_TARGET_ERROR', len(selected), errors[0])
        self.assertAlmostEqual(arm.pose.bones['hand_fk.R']['mirror_test_value'], 0.25)
        self.assertAlmostEqual(arm.pose.bones['root'].matrix.translation.x, -0.35, places=5)
        snapshot.apply(arm, mirror.mirror_pose(arm, selected, mode='SWAP'))
        worst = max(matrix_error(matrix, arm.matrix_world @ arm.pose.bones[name].matrix)
                    for name, matrix in before.items())
        worst_def = max(matrix_error(matrix, arm.matrix_world @ arm.pose.bones[name].matrix)
                        for name, matrix in defs.items())
        print('RIG_STUDIO_SWAP', bpy.app.version_string, 'selected_fk', len(selected), 'controls', len(names),
              'bones', len(mapping), 'max_error', worst, 'def_max_error', worst_def)
        for name, values in props.items():
            self.assertEqual(snapshot.numeric_properties(arm.pose.bones[name]), values)
        self.assertLessEqual(worst, 1e-4)
        self.assertLessEqual(errors[0][0], 1e-4)
        # DEF는 리그의 IK·트위스트 재평가 수치도 포함하므로 진단값만 출력한다.


if __name__ == '__main__':
    if not bpy.app.background:
        raise RuntimeError('별도 백그라운드 Blender에서만 실행합니다.')
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(RigStudioMirror))
    if not result.wasSuccessful():
        raise RuntimeError('실제 Rig Studio 미러 검사 실패')
