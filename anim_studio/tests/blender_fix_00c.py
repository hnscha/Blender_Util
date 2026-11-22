# SPDX-License-Identifier: GPL-3.0-or-later
"""00c 회귀 검사. Blender --background --factory-startup --python 실행."""
import sys
import json
import unittest
from types import SimpleNamespace
from time import perf_counter
from statistics import median
from unittest.mock import patch
from contextlib import nullcontext
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy
import anim_studio
from anim_studio.core import bake, fcurve
from anim_studio.ops import hotkey_ops
from anim_studio.ui import panels
from blender_smoke import Engines


class Fixes(unittest.TestCase):
    setUp = Engines.setUp

    @classmethod
    def setUpClass(cls):
        anim_studio.register()

    @classmethod
    def tearDownClass(cls):
        anim_studio.unregister()

    def test_01_unkeyed_pose(self):
        arm = self.arm
        a, b = arm.pose.bones['root'], arm.pose.bones['hand.L']
        a['amount'] = b['amount'] = 0.0
        for pb in (a, b):
            for path, index, value in fcurve.transform_channels(pb):
                arm.keyframe_insert(path, index=index, frame=5, group=pb.name)
            pb.keyframe_insert('["amount"]', frame=5)
        other = arm.copy()
        other.data = arm.data.copy()
        bpy.context.scene.collection.objects.link(other)
        for operation in ('set', 'delete', 'all', 'bake'):
            with self.subTest(operation=operation):
                bpy.context.scene.frame_set(5)
                a.rotation_quaternion = (0.70710678, 0.70710678, 0, 0)
                a.lock_rotation = (operation == 'all',) * 3
                a.location.y = 1.25
                b.location.x = 3.0
                a['amount'], b['amount'] = 2.0, 4.0
                other.pose.bones['hand.L'].location.x = 7.0
                other.pose.bones['hand.L']['amount'] = 8.0
                bpy.context.view_layer.update()
                events = []
                def frame_changed(*_args):
                    events.append(1)
                bpy.app.handlers.frame_change_pre.append(frame_changed)
                try:
                    if operation == 'bake':
                        bake.bake(arm, [a.name], [5], channels={'location'})
                    else:
                        targets = [(arm, a)] if operation != 'all' else [(arm, pb) for pb in arm.pose.bones]
                        channels = {'location'} if operation != 'all' else fcurve.TRANSFORMS
                        hotkey_ops._key_targets(bpy.context, targets, channels, delete=operation == 'delete')
                    bpy.context.view_layer.update()
                finally:
                    bpy.app.handlers.frame_change_pre.remove(frame_changed)
                print('POSE', operation, tuple(a.rotation_quaternion), b.location.x,
                      a['amount'], b['amount'], other.pose.bones['hand.L'].location.x)
                self.assertEqual(events, [])
                self.assertAlmostEqual(a.rotation_quaternion.x, 0.70710678, places=6)
                self.assertAlmostEqual(b.location.x, 3.0)
                self.assertEqual((a['amount'], b['amount']), (2.0, 4.0))
                self.assertAlmostEqual(other.pose.bones['hand.L'].location.x, 7.0)
                self.assertEqual(other.pose.bones['hand.L']['amount'], 8.0)
                curve = fcurve.channelbag(arm).fcurves.find(a.path_from_id('location'), index=1)
                if operation == 'delete':
                    self.assertIsNone(curve)
                else:
                    self.assertAlmostEqual(curve.evaluate(5), 1.25)

    def test_02_empty_curves(self):
        cb = fcurve.channelbag(self.arm)
        for index in range(3):
            fcurve.write_keys(cb, 'location', index, [5], [1], 'Object')
        self.assertEqual(fcurve.delete_keys(cb, 'location', 0, 5, 5), 1)
        self.assertIsNone(cb.fcurves.find('location', index=0))
        self.assertEqual(fcurve.delete_keys_outside(cb, 10, 20), 2)
        self.assertEqual(len(cb.fcurves), 0)

    def test_03_orphan_paths(self):
        cb = fcurve.channelbag(self.arm)
        for path in ('location', 'pose.bones["gone"].location'):
            curve = cb.fcurves.new(path, index=0)
            for frame in (-5, 5, 20):
                curve.keyframe_points.insert(frame, 1)
        bpy.context.scene.use_preview_range = True
        bpy.context.scene.frame_preview_start = 1
        bpy.context.scene.frame_preview_end = 10
        try:
            hotkey_ops.ANIMSTUDIO_OT_delete_keys_outside.run(None, bpy.context)
        finally:
            print('ORPHAN_REMAINING', [(c.data_path, len(c.keyframe_points)) for c in cb.fcurves])
            bpy.context.scene.use_preview_range = False
        self.assertEqual([[p.co.x for p in c.keyframe_points] for c in cb.fcurves], [[5], [5]])

    def test_04_unexpected_errors(self):
        def broken(_context):
            raise TypeError('programming defect')
        operator = SimpleNamespace(run=broken, report=lambda *_args: None)
        with self.assertRaises(TypeError):
            hotkey_ops.StudioOperator.execute(operator, bpy.context)

    def test_03_preflight_before_any_deletion(self):
        from anim_studio.core.errors import StudioError
        other = self.arm.copy()
        other.data = self.arm.data.copy()
        bpy.context.scene.collection.objects.link(other)
        other.select_set(True)
        for obj in (self.arm, other):
            obj.animation_data_clear()
            fcurve.write_keys(fcurve.channelbag(obj), 'location', 0, [-5, 5, 20], [1, 2, 3], 'Object')
        original_skip = fcurve.channel_skip_reason
        def rejected(obj, path, index):
            if obj == other:
                raise StudioError('사전 검사 실패')
            return original_skip(obj, path, index)
        with patch.object(fcurve, 'channel_skip_reason', side_effect=rejected):
            with self.assertRaises(StudioError):
                hotkey_ops.ANIMSTUDIO_OT_delete_keys_outside.run(None, bpy.context)
        for obj in (self.arm, other):
            self.assertEqual(len(fcurve.channelbag(obj).fcurves[0].keyframe_points), 3)

    def test_04_preconditions(self):
        from anim_studio.core.errors import StudioError
        bpy.context.scene.camera = self.arm
        with self.assertRaisesRegex(StudioError, '카메라'):
            hotkey_ops._camera(bpy.context)
        with self.assertRaisesRegex(StudioError, '아마추어'):
            hotkey_ops.ANIMSTUDIO_OT_choose_armature.run(SimpleNamespace(armature='gone'), bpy.context)
        self.arm.hide_set(True)
        with self.assertRaisesRegex(StudioError, '아마추어'):
            hotkey_ops._activate_armature(bpy.context, self.arm, pose=True)

    def test_05_performance(self):
        arm = self.arm
        bpy.ops.object.mode_set(mode='EDIT')
        for bone in list(arm.data.edit_bones):
            arm.data.edit_bones.remove(bone)
        for i in range(60):
            bone = arm.data.edit_bones.new(f'b{i}')
            bone.head, bone.tail = (i, 0, 0), (i, 1, 0)
        bpy.ops.object.mode_set(mode='OBJECT')
        cb = fcurve.channelbag(arm)
        entries = [(pb.name, path, index, value) for pb in arm.pose.bones
                   for path, index, value in fcurve.transform_channels(pb)]
        self.assertEqual(len(entries), 600)
        for name, path, index, value in entries:
            curve = cb.fcurves.new(path, index=index, group_name=name)
            curve.keyframe_points.add(1000)
            curve.keyframe_points.foreach_set('co', [v for frame in range(1, 1001) for v in (frame, value)])
            curve.update()
        bulk, native = [], []
        for _ in range(3):
            started = perf_counter()
            for name, path, index, value in entries:
                fcurve.write_keys(cb, path, index, [500], [value], name)
            bulk.append(perf_counter() - started)
            bpy.context.scene.frame_set(500)
            started = perf_counter()
            hotkey_ops._key_targets(bpy.context, [(arm, pb) for pb in arm.pose.bones], fcurve.TRANSFORMS)
            native.append(perf_counter() - started)
        print('PERFORMANCE_600x1000', 'bulk', bulk, 'median', median(bulk),
              'native', native, 'median', median(native))

    def test_06_com_visibility(self):
        from anim_studio.core.errors import StudioError
        arm = self.arm
        pb = arm.pose.bones['root']
        arm.data['rig_studio_info'] = json.dumps({'version': 'test'})
        pb.bone['rig_studio'] = json.dumps({'type': 'CTRL', 'role': 'com', 'side': 'C'})
        collection = arm.data.collections.new('COM')
        collection.assign(pb.bone)
        for hide_bone in (True, False):
            with self.subTest(hide_bone=hide_bone):
                pb.hide = hide_bone
                collection.is_visible = hide_bone
                with self.assertRaises(StudioError):
                    hotkey_ops.ANIMSTUDIO_OT_select_com.run(None, bpy.context)
                self.assertEqual(pb.hide, hide_bone)
                self.assertEqual(collection.is_visible, hide_bone)

    def test_07_focus_without_redraw(self):
        ui = SimpleNamespace(type='UI', active_panel_category='Item', is_property_readonly=lambda _p: False)
        area = SimpleNamespace(spaces=SimpleNamespace(active=SimpleNamespace(show_region_ui=False)),
                               regions=[ui, SimpleNamespace(type='WINDOW')])
        context = SimpleNamespace(area=area, temp_override=lambda **_kw: nullcontext())
        with patch.object(bpy.ops.wm, 'redraw_timer') as redraw:
            hotkey_ops.ANIMSTUDIO_OT_focus_panel.run(None, context)
            redraw.assert_not_called()
        self.assertEqual(ui.active_panel_category, 'Anim Studio')
        ui.is_property_readonly = lambda _p: True
        self.assertIn('한 번 더', hotkey_ops.ANIMSTUDIO_OT_focus_panel.run(None, context))

    def test_08_draw_sharing(self):
        class Layout:
            def __init__(self):
                self.labels, self.operators = [], []
            def __getattr__(self, _name):
                return lambda *_args, **_kwargs: self
            def label(self, **kwargs):
                self.labels.append(kwargs.get('text'))
            def operator(self, operator, **_kwargs):
                self.operators.append(operator)
                return SimpleNamespace()
        layout = Layout()
        bpy.context.window_manager.anim_studio_last = '검사 결과'
        info = {'All Controls Key': ('K', ['충돌 검사'])}
        area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'UI')
        with bpy.context.temp_override(area=area, region=region), \
                patch.object(panels, 'shortcut_info', return_value=info) as lookup:
            panels.ANIMSTUDIO_PT_main.draw(SimpleNamespace(layout=layout), bpy.context)
            for group in hotkey_ops.BUTTONS:
                panels._grid(layout, bpy.context, group)
            self.assertEqual(lookup.call_count, 1)
        self.assertIn('검사 결과', layout.labels)
        self.assertIn('키 겹침 · 충돌 검사', layout.labels)
        self.assertIn('anim_studio.open_keymap', layout.operators)

    def test_08_reuse_slot(self):
        cb = fcurve.channelbag(self.arm)
        action = cb.id_data
        handle = cb.slot_handle
        self.arm.animation_data.action_slot = None
        count = len(action.slots)
        cb = fcurve.channelbag(self.arm)
        self.assertEqual(len(action.slots), count)
        self.assertEqual(cb.slot_handle, handle)


if __name__ == '__main__':
    names = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    suite = (unittest.TestSuite(Fixes(name) for name in names) if names else
             unittest.defaultTestLoader.loadTestsFromTestCase(Fixes))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
