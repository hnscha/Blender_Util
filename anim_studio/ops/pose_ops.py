# SPDX-License-Identifier: GPL-3.0-or-later
import json

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty

from ..core import mirror, pose_tools as pose, snapshot
from ..core.context import active_mode_controls, frame_range
from ..core.errors import StudioError
from .hotkey_ops import StudioOperator

MIRROR_MODES = (('SWAP', 'Swap', '좌우 자세 교환'),
                ('LEFT_TO_RIGHT', 'L → R', '왼쪽 포즈를 오른쪽으로'),
                ('RIGHT_TO_LEFT', 'R → L', '오른쪽 포즈를 왼쪽으로'))


def history(wm, index=-1):
    index = wm.anim_studio_history_index if index < 0 else index
    if not 0 <= index < len(wm.anim_studio_history):
        raise StudioError('포즈 복사 기록을 선택하세요.')
    return json.loads(wm.anim_studio_history[index].payload)


class PoseOperator(StudioOperator):
    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE' and context.active_object is not None


class ANIMSTUDIO_PoseRecord(bpy.types.PropertyGroup):
    label: StringProperty(options={'SKIP_SAVE'})
    payload: StringProperty(options={'SKIP_SAVE'})


class ANIMSTUDIO_OT_pose_copy(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.pose_copy'
    bl_label = 'Anim Studio: Copy'
    bl_description = 'Copy\n선택 본의 로컬·월드 자세를 세션 기록에 복사합니다'
    bl_options = {'REGISTER'}

    def run(self, context):
        arm, names = pose.selected(context)
        names, skipped = active_mode_controls(arm, names)
        if not names:
            raise StudioError('현재 모드의 컨트롤을 선택하세요.')
        data = {space: snapshot.capture(arm, names, space) for space in ('LOCAL', 'WORLD')}
        wm = context.window_manager
        item = wm.anim_studio_history.add()
        item.label = f'{len(names)} bones · F{context.scene.frame_current_final:g}'
        item.payload = json.dumps(data, ensure_ascii=False)
        wm.anim_studio_history.move(len(wm.anim_studio_history) - 1, 0)
        while len(wm.anim_studio_history) > 10:
            wm.anim_studio_history.remove(10)
        wm.anim_studio_history_index = 0
        from ..core.context import mode_skip_message
        return item.label + (' · ' + mode_skip_message(skipped) if skipped else '')


class ANIMSTUDIO_OT_pose_history_delete(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.pose_history_delete'
    bl_label = 'Anim Studio: Delete Pose Record'
    bl_description = 'Delete Record\n이 기록을 삭제합니다. Shift를 누르면 기록 전체를 지웁니다'
    bl_options = {'REGISTER'}
    index: IntProperty(default=-1)
    clear_all: BoolProperty(default=False, options={'SKIP_SAVE'})

    def invoke(self, context, event):
        self.clear_all = event.shift
        return self.execute(context)

    def run(self, context):
        wm = context.window_manager
        if self.clear_all:
            wm.anim_studio_history.clear()
        else:
            index = wm.anim_studio_history_index if self.index < 0 else self.index
            history(wm, index)
            wm.anim_studio_history.remove(index)
        wm.anim_studio_history_index = min(wm.anim_studio_history_index, max(0, len(wm.anim_studio_history) - 1))
        return f'{len(wm.anim_studio_history)} poses'


class ANIMSTUDIO_OT_pose_paste(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.pose_paste'
    bl_label = 'Anim Studio: Paste'
    bl_description = 'Paste\n기록에 있는 선택 본에 자세를 붙이고 현재 프레임 키를 넣습니다'
    index: IntProperty(default=-1, options={'SKIP_SAVE'})

    def run(self, context):
        arm, names = pose.selected(context)
        wm = context.window_manager
        data = history(wm, self.index)
        return pose.apply_current(arm, pose.targets(data[wm.anim_studio_paste_space], names))


class ANIMSTUDIO_OT_pose_paste_opposite(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.pose_paste_opposite'
    bl_label = 'Anim Studio: Paste Opposite'
    bl_description = 'Paste Opposite\n선택 본 또는 그 짝의 로컬 기록을 반대편에 붙이고 키를 넣습니다'

    def run(self, context):
        arm, names = pose.selected(context)
        mapping = mirror.pairs(arm)
        data = history(context.window_manager)['LOCAL']
        stored = pose.targets(data, set(names) | {mapping[n] for n in names})
        return pose.apply_current(arm, pose.mirror_saved(arm, stored))


class ANIMSTUDIO_OT_pose_paste_range(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.pose_paste_range'
    bl_label = 'Anim Studio: Paste Range'
    bl_description = 'Paste Range\n선택 본에 이미 키가 있는 구간 프레임에만 포즈를 붙입니다'

    def run(self, context):
        arm, names = pose.selected(context)
        wm = context.window_manager
        stored = pose.targets(history(wm)[wm.anim_studio_paste_space], names)
        if not stored:
            raise StudioError('선택한 본이 포즈 기록에 없습니다.')
        start, end, _reason = frame_range(context)
        frames = pose.key_frames(arm, names, start, end)
        if not frames:
            raise StudioError('구간 안에 선택 본의 키가 없습니다.')
        return pose.range_message(context, pose.bake_pose(arm, list(stored), frames, lambda _f: stored))


class ANIMSTUDIO_OT_mirror_pose(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.mirror_pose'
    bl_label = 'Anim Studio: Mirror Pose'
    bl_description = 'Mirror Pose\n현재 모드의 선택 본과 짝을 미러하고 현재 프레임 키를 넣습니다'

    def run(self, context):
        arm, names = pose.selected(context)
        return pose.apply_current(arm, mirror.mirror_pose(arm, names, context.window_manager.anim_studio_mirror_mode))


class ANIMSTUDIO_OT_mirror_range(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.mirror_range'
    bl_label = 'Anim Studio: Mirror Range'
    bl_description = 'Mirror Range\n선택 본과 짝의 키 프레임에서 구간 자세를 미러합니다'

    def run(self, context):
        arm, names = pose.selected(context)
        mapping = mirror.pairs(arm)
        names = sorted(set(names) | {mapping[n] for n in names})
        start, end, _reason = frame_range(context)
        frames = pose.key_frames(arm, names, start, end)
        if not frames:
            raise StudioError('구간 안에 선택 본의 키가 없습니다.')
        mode = context.window_manager.anim_studio_mirror_mode
        result = pose.bake_pose(arm, names, frames, lambda _f: mirror.mirror_pose(arm, names, mode))
        return pose.range_message(context, result)


class ANIMSTUDIO_OT_mirror_loop(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.mirror_loop'
    bl_label = 'Anim Studio: Mirror Loop'
    bl_description = 'Mirror Loop\n원본 반주기의 키를 미러하여 반대 반주기에 쓰고 루프를 닫습니다'
    half: EnumProperty(name='Half', items=(('FIRST', 'First', '전반부 → 후반부'),
                                          ('SECOND', 'Second', '후반부 → 전반부')))

    def run(self, context):
        arm, names = pose.selected(context)
        return pose.range_message(context, pose.loop(arm, names, context, half=self.half))


class ANIMSTUDIO_OT_mirror_loop_single(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.mirror_loop_single'
    bl_label = 'Anim Studio: Mirror Single Frame'
    bl_description = 'Mirror Single Frame\n현재 미기록 자세도 포함해 반 주기 떨어진 프레임에 미러 키를 넣습니다'

    def run(self, context):
        arm, names = pose.selected(context)
        return pose.range_message(context, pose.loop(arm, names, context, single=True))


class ANIMSTUDIO_OT_key_match(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.key_match'
    bl_label = 'Anim Studio: Key Match'
    bl_description = 'Key Match\n이전·다음 키의 위치와 선택한 경우 회전을 현재 프레임에 맞춥니다'
    direction: EnumProperty(name='Direction', items=(('PREV', 'Prev', '이전 키'), ('NEXT', 'Next', '다음 키')))

    def run(self, context):
        arm, names = pose.selected(context)
        wm = context.window_manager
        key_names, _skipped = active_mode_controls(arm, names)
        frame = pose.neighbor_frame(arm, key_names, context.scene.frame_current_final, self.direction)
        target = pose.measure(arm, names, [frame])[frame] if wm.anim_studio_km_world else pose.evaluate_local(arm, names, frame)
        target.custom_properties = {}
        return pose.apply_current(arm, target, {'location', 'rotation'} if wm.anim_studio_km_rot else {'location'})


_CLASSES = (ANIMSTUDIO_PoseRecord, ANIMSTUDIO_OT_pose_copy, ANIMSTUDIO_OT_pose_history_delete,
            ANIMSTUDIO_OT_pose_paste, ANIMSTUDIO_OT_pose_paste_opposite, ANIMSTUDIO_OT_pose_paste_range,
            ANIMSTUDIO_OT_mirror_pose, ANIMSTUDIO_OT_mirror_range, ANIMSTUDIO_OT_mirror_loop,
            ANIMSTUDIO_OT_mirror_loop_single, ANIMSTUDIO_OT_key_match)
_PROPS = ('anim_studio_history', 'anim_studio_history_index', 'anim_studio_paste_space',
          'anim_studio_mirror_mode', 'anim_studio_km_rot', 'anim_studio_km_world')


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    wm = bpy.types.WindowManager
    wm.anim_studio_history = CollectionProperty(type=ANIMSTUDIO_PoseRecord, options={'SKIP_SAVE'})
    wm.anim_studio_history_index = IntProperty(default=0, options={'SKIP_SAVE'})
    wm.anim_studio_paste_space = EnumProperty(name='Space', description='붙일 포즈의 기준 공간',
        items=(('LOCAL', 'Local', '본 로컬 채널'), ('WORLD', 'World', '월드 자세')), default='LOCAL', options={'SKIP_SAVE'})
    wm.anim_studio_mirror_mode = EnumProperty(name='Mode', description='미러 방향', items=MIRROR_MODES, options={'SKIP_SAVE'})
    wm.anim_studio_km_rot = BoolProperty(name='Rot', description='위치와 함께 회전도 맞춤', default=True, options={'SKIP_SAVE'})
    wm.anim_studio_km_world = BoolProperty(name='World', description='참조 프레임의 월드 자세로 맞춤', default=False, options={'SKIP_SAVE'})


def unregister():
    for name in reversed(_PROPS):
        delattr(bpy.types.WindowManager, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
