# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import EnumProperty, FloatProperty

from ..core import fcurve, pose_tools as pose, snapshot
from ..core.context import active_mode_controls, busy
from ..core.errors import StudioError
from .hotkey_ops import set_result
from .pose_ops import PoseOperator, history

TARGETS = (('NEIGHBORS', 'Neighbors', '이전 키와 다음 키 사이', 'PREV_KEYFRAME', 0),
           ('DEFAULT', 'Default', '현재 자세에서 기본 자세로', 'LOOP_BACK', 1),
           ('MIRROR', 'Mirror', '현재 자세에서 미러 자세로', 'MOD_MIRROR', 2),
           ('BUFFER', 'Buffer', '현재 자세에서 선택한 복사 기록으로', 'PASTEDOWN', 3))


def endpoints(context, arm, names, target):
    names, _skipped = active_mode_controls(arm, names)
    if not names:
        raise StudioError('현재 모드의 컨트롤을 선택하세요.')
    return pose.tween_endpoints(arm, names, target,
                               history(context.window_manager) if target == 'BUFFER' else None)


class ANIMSTUDIO_OT_tween_set(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.tween_set'
    bl_label = 'Anim Studio: Tween'
    bl_description = 'Tween\n선택한 비율로 로컬 자세를 보간하고 현재 프레임 키를 넣습니다'
    target: EnumProperty(name='Target', description='보간할 목표', items=TARGETS)
    factor: FloatProperty(name='Factor', description='보간 비율', default=0.5, min=-0.5, max=1.5)

    def run(self, context):
        arm, names = pose.selected(context)
        a, b = endpoints(context, arm, names, self.target)
        return pose.apply_current(arm, pose.blend(a, b, self.factor))


class ANIMSTUDIO_OT_tween(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.tween'
    bl_label = 'Anim Studio: Tween…'
    bl_description = 'Tween…\n좌우 드래그 · Ctrl 오버슈트 · Shift 25% · 휠 목표 · 클릭 확정 · Esc 취소'
    target: EnumProperty(name='Target', description='보간할 목표', items=TARGETS)
    factor: FloatProperty(name='Factor', description='보간 비율', default=0.5, min=-0.5, max=1.5)

    def _restore(self, context):
        arm = context.scene.objects.get(self._arm_name)
        if arm is None:
            return
        for name, channels in self._original_channels.items():
            pb = arm.pose.bones.get(name)
            if pb is None:
                continue
            for prop, index, value in channels:
                getattr(pb, prop)[index] = value
            for prop, value in self._original_properties[name].items():
                pb[prop] = value
        arm.update_tag(refresh={'OBJECT'})
        context.view_layer.update()

    def _finish(self, context, restore=False):
        if self._closed:
            return
        self._closed = True
        try:
            if restore:
                self._restore(context)
        finally:
            if self._area is not None:
                self._area.header_text_set(None)
            self._busy.__exit__(None, None, None)

    def _header(self):
        label = next(item[1] for item in TARGETS if item[0] == self.target)
        self._area.header_text_set(f'Tween · {label} · {self.factor * 100:g}%')

    def invoke(self, context, event):
        try:
            arm, names = pose.selected(context)
            names, _skipped = active_mode_controls(arm, names)
            if not names:
                raise StudioError('현재 모드의 컨트롤을 선택하세요.')
            self._arm_name, self._names = arm.name, names
            self._closed = False
            self._area = context.area
            self._original_channels = {n: [(path.rsplit('.', 1)[-1], i, v)
                                          for path, i, v in fcurve.transform_channels(arm.pose.bones[n])]
                                       for n in names}
            self._original_properties = {n: snapshot.numeric_properties(arm.pose.bones[n]) for n in names}
            if not self.properties.is_property_set('target'):
                self.target = context.window_manager.anim_studio_tween_target
            self._busy = busy()
            self._busy.__enter__()
            try:
                self._a, self._b = endpoints(context, arm, names, self.target)
                self.factor = 0.5 if self.target == 'NEIGHBORS' else 0.0
                self._start_factor, self._mouse_x = self.factor, event.mouse_x
                self._header()
                context.window_manager.modal_handler_add(self)
                return {'RUNNING_MODAL'}
            except BaseException:
                self._finish(context, restore=True)
                raise
        except StudioError as error:
            self.report({'ERROR'}, str(error))
            set_result(context, str(error), False)
            return {'CANCELLED'}

    def modal(self, context, event):
        try:
            if event.type in {'RIGHTMOUSE', 'ESC', 'WINDOW_DEACTIVATE'}:
                self._finish(context, restore=True)
                return {'CANCELLED'}
            arm = context.scene.objects.get(self._arm_name)
            if arm is None:
                raise StudioError('트윈 대상 아마추어가 없습니다.')
            if event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
                result = pose.apply_current(arm, pose.blend(self._a, self._b, self.factor))
                self._finish(context)
                set_result(context, result)
                return {'FINISHED'}
            if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                self._restore(context)
                values = [item[0] for item in TARGETS]
                index = (values.index(self.target) + (1 if event.type == 'WHEELUPMOUSE' else -1)) % len(values)
                self.target = values[index]
                self._a, self._b = endpoints(context, arm, self._names, self.target)
                self._start_factor, self._mouse_x = self.factor, event.mouse_x
            if event.type == 'MOUSEMOVE':
                value = self._start_factor + (event.mouse_x - self._mouse_x) / 300.0
                lo, hi = (-0.5, 1.5) if event.ctrl else (0.0, 1.0)
                self.factor = min(hi, max(lo, round(value * 4) / 4 if event.shift else value))
            if event.type in {'MOUSEMOVE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                pose.apply_current(arm, pose.blend(self._a, self._b, self.factor), key=False)
                self._header()
            return {'RUNNING_MODAL'}
        except StudioError as error:
            self._finish(context, restore=True)
            self.report({'ERROR'}, str(error))
            set_result(context, str(error), False)
            return {'CANCELLED'}
        except BaseException:
            self._finish(context, restore=True)
            raise

    def cancel(self, context):
        self._finish(context, restore=True)

    def run(self, context):
        arm, names = pose.selected(context)
        a, b = endpoints(context, arm, names, self.target)
        return pose.apply_current(arm, pose.blend(a, b, self.factor))


_CLASSES = (ANIMSTUDIO_OT_tween_set, ANIMSTUDIO_OT_tween)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.anim_studio_tween_target = EnumProperty(
        name='Target', description='트윈 목표', items=TARGETS, options={'SKIP_SAVE'})


def unregister():
    del bpy.types.WindowManager.anim_studio_tween_target
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
