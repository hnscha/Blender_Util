# SPDX-License-Identifier: GPL-3.0-or-later
import json

import bpy
from bpy.props import BoolProperty, StringProperty

from ..core import bake, pose_tools as pose, snapshot, space
from ..core.context import frame_range, mode_skip_message
from ..core.errors import StudioError
from .hotkey_ops import StudioOperator
from .pose_ops import PoseOperator


def channels(wm):
    result = {channel for channel, prop in (('location', 'pos'), ('rotation', 'rot'), ('scale', 'scale'))
              if getattr(wm, 'anim_studio_align_' + prop)}
    if not result:
        raise StudioError('Position, Rotation, Scale 중 하나 이상을 켜세요.')
    return result


class ANIMSTUDIO_OT_temp_pivot_create(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.temp_pivot_create'
    bl_label = 'Anim Studio: Create Temp Pivot'
    bl_description = 'Create\n커서 위치에 임시 피봇을 만듭니다. 재오픈하면 굽지 않은 피봇 변경은 사라집니다'

    def run(self, context):
        wm = context.window_manager
        names, skipped = space.create_pivot(context, wm.anim_studio_follow_pos, wm.anim_studio_follow_rot)
        return f'Target: {len(names)} bones · Pivot: AS_Pivot' + (' · ' + mode_skip_message(skipped) if skipped else '')


class PivotOperator(StudioOperator):
    @classmethod
    def poll(cls, context):
        return any(obj.get('anim_studio_pivot') for obj in context.scene.objects)


class ANIMSTUDIO_OT_temp_pivot_apply(PivotOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.temp_pivot_apply'
    bl_label = 'Anim Studio: Apply Temp Pivot'
    bl_description = 'Apply\n현재 피봇 자세를 키로 굽고 피봇과 임시 컨스트레인트를 정리합니다'

    def run(self, context):
        result = space.apply_pivot(context, [context.scene.frame_current_final])
        return f'{len(result.baked)} bones · F{context.scene.frame_current_final:g} · 피봇 정리'


class ANIMSTUDIO_OT_temp_pivot_bake(PivotOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.temp_pivot_bake'
    bl_label = 'Anim Studio: Bake Temp Pivot'
    bl_description = 'Bake Range\n피봇 자세를 구간 매 프레임 키로 굽고 임시 항목을 정리합니다'

    def run(self, context):
        start, end, _reason = frame_range(context)
        return pose.range_message(context, space.apply_pivot(context, range(start, end + 1)))


class ANIMSTUDIO_OT_temp_pivot_delete(PivotOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.temp_pivot_delete'
    bl_label = 'Anim Studio: Delete Temp Pivot'
    bl_description = 'Delete\n키를 굽지 않고 피봇을 제거하여 원래 애니메이션으로 돌아갑니다'

    def run(self, context):
        return f'{space.delete_pivot(context)} bones · 피봇 삭제'


class ANIMSTUDIO_OT_world_lock_range(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.world_lock_range'
    bl_label = 'Anim Studio: Lock Range'
    bl_description = 'Lock Range\n실행 직전 월드 자세를 구간 매 프레임 유지하도록 선택 채널을 굽습니다'

    def run(self, context):
        arm, names = pose.selected(context)
        selected_channels = channels(context.window_manager)
        fixed = pose.current(arm, names, 'WORLD')
        fixed.custom_properties = {}
        start, end, _reason = frame_range(context)
        result = bake.bake(arm, names, range(start, end + 1), lambda _f: fixed,
                           channels=selected_channels, preserve_undo=True)
        return pose.range_message(context, result)


class ANIMSTUDIO_OT_align_set(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.align_set'
    bl_label = 'Anim Studio: Set from Selection'
    bl_description = 'Set from Selection\n선택 본을 Source, 활성 본 또는 오브젝트를 Target으로 기억합니다'
    bl_options = {'REGISTER'}

    def run(self, context):
        state = space.align_selection(context)
        context.window_manager.anim_studio_align = json.dumps(state, ensure_ascii=False)
        return f'Source: {len(state["sources"])} bones · Target: {state["target_bone"] or state["target_object"]}'


class AlignOperator(StudioOperator):
    @classmethod
    def poll(cls, context):
        return bool(context.window_manager.anim_studio_align)


class ANIMSTUDIO_OT_align_now(AlignOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.align_now'
    bl_label = 'Anim Studio: Align Now'
    bl_description = 'Align Now\n기억한 Source 본을 현재 Target 월드 자세에 맞추고 키를 넣습니다'

    def run(self, context):
        arm, names, target, bone = space.align_state(context)
        matrix = space.target_matrix(target, bone)
        return pose.apply_current(arm, snapshot.PoseTargets({n: matrix for n in names}, {}), channels(context.window_manager))


class ANIMSTUDIO_OT_align_range(AlignOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.align_range'
    bl_label = 'Anim Studio: Align Range'
    bl_description = 'Align Range\n기억한 Source 본을 구간 매 프레임의 Target 월드 자세로 굽습니다'

    def run(self, context):
        arm, names, target, bone = space.align_state(context)
        selected_channels = channels(context.window_manager)
        start, end, _reason = frame_range(context)
        def target_fn(_frame):
            matrix = space.target_matrix(target, bone)
            return {n: matrix for n in names}
        return pose.range_message(context, bake.bake(arm, names, range(start, end + 1), target_fn,
                                                    channels=selected_channels, preserve_undo=True))


_CLASSES = (ANIMSTUDIO_OT_temp_pivot_create, ANIMSTUDIO_OT_temp_pivot_apply, ANIMSTUDIO_OT_temp_pivot_bake,
            ANIMSTUDIO_OT_temp_pivot_delete, ANIMSTUDIO_OT_world_lock_range, ANIMSTUDIO_OT_align_set,
            ANIMSTUDIO_OT_align_now, ANIMSTUDIO_OT_align_range)
_PROPS = ('anim_studio_follow_pos', 'anim_studio_follow_rot', 'anim_studio_align_pos',
          'anim_studio_align_rot', 'anim_studio_align_scale', 'anim_studio_align')


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    wm = bpy.types.WindowManager
    for prop, label, description, default in (
            ('anim_studio_follow_pos', 'Follow Pos', '피봇의 위치를 따라감', True),
            ('anim_studio_follow_rot', 'Follow Rot', '피봇의 회전을 따라감', True),
            ('anim_studio_align_pos', 'Position', '위치 채널을 기록', True),
            ('anim_studio_align_rot', 'Rotation', '회전 채널을 기록', True),
            ('anim_studio_align_scale', 'Scale', '스케일 채널을 기록', False)):
        setattr(wm, prop, BoolProperty(name=label, description=description, default=default, options={'SKIP_SAVE'}))
    wm.anim_studio_align = StringProperty(options={'SKIP_SAVE'})


def unregister():
    for name in reversed(_PROPS):
        delattr(bpy.types.WindowManager, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
