# SPDX-License-Identifier: GPL-3.0-or-later
import json

import bpy

from . import bake, fcurve, pose_tools as pose, temp
from .context import active_mode_controls, busy
from .errors import StudioError


def pivot_state(context):
    pivots = [obj for obj in context.scene.objects if obj.get('anim_studio_pivot')]
    if not pivots:
        return None
    if len(pivots) != 1:
        raise StudioError('임시 피봇이 여러 개입니다. 먼저 불필요한 피봇을 정리하세요.')
    pivot = pivots[0]
    arm = context.scene.objects.get(pivot.get('anim_studio_arm', ''))
    if arm is None or arm.type != 'ARMATURE':
        raise StudioError('임시 피봇의 아마추어를 찾을 수 없습니다.')
    names = [pb.name for pb in arm.pose.bones if any(c.name.startswith(temp.PREFIX)
             and c.type == 'CHILD_OF' and c.target == pivot for c in pb.constraints)]
    return pivot, arm, names


def create_pivot(context, follow_pos=True, follow_rot=True):
    if pivot_state(context) is not None:
        raise StudioError('기존 임시 피봇을 Apply 또는 Delete로 정리하세요.')
    if not follow_pos and not follow_rot:
        raise StudioError('Follow Pos 또는 Follow Rot를 켜세요.')
    arm, names = pose.selected(context)
    names, skipped = active_mode_controls(arm, names)
    if not names:
        raise StudioError('현재 모드의 컨트롤을 선택하세요.')
    writable = pose.writable_channels(arm, names, fcurve.TRANSFORMS)
    for name in names:
        pb = arm.pose.bones[name]
        if not writable[name]:
            raise StudioError('기록할 채널이 없는 본입니다: ' + name)
        if pb.is_in_ik_chain or any(not c.mute and c.influence > 0 for c in pb.constraints):
            raise StudioError('활성 컨스트레인트·IK 체인 본에는 피봇을 만들 수 없습니다: ' + name)
    size = sum(arm.pose.bones[n].length for n in names) / len(names) * 0.5
    from .snapshot import capture
    original = capture(arm, names, 'LOCAL')
    with busy(), temp.session() as session:
        pivot = session.empty('AS_Pivot', context.scene.cursor.matrix.copy(), display='SPHERE', size=size)
        pivot['anim_studio_pivot'] = True
        pivot['anim_studio_arm'] = arm.name
        pivot['anim_studio_original'] = json.dumps(original, ensure_ascii=False)
        for name in names:
            con = session.constraint(arm.pose.bones[name], 'CHILD_OF', target=pivot)
            con.use_location_x = con.use_location_y = con.use_location_z = follow_pos
            con.use_rotation_x = con.use_rotation_y = con.use_rotation_z = follow_rot
            con.use_scale_x = con.use_scale_y = con.use_scale_z = False
            con.set_inverse_pending = True
            context.view_layer.update()
        session.keep()
    pivot.select_set(True)
    context.view_layer.objects.active = pivot
    return names, skipped


def remove_pivot(context):
    state = pivot_state(context)
    if state is None:
        raise StudioError('임시 피봇이 없습니다.')
    pivot, arm, names = state
    for name in names:
        pb = arm.pose.bones[name]
        for con in list(pb.constraints):
            if con.name.startswith(temp.PREFIX) and con.type == 'CHILD_OF' and con.target == pivot:
                pb.constraints.remove(con)
    bpy.data.objects.remove(pivot, do_unlink=True)
    temp._remove_empty_collection()
    context.view_layer.objects.active = arm
    arm.select_set(True)
    if arm.mode != 'POSE':
        bpy.ops.object.mode_set(mode='POSE')
    return arm, names


def apply_pivot(context, frames):
    state = pivot_state(context)
    if state is None:
        raise StudioError('임시 피봇이 없습니다.')
    _pivot, arm, names = state
    if not names:
        raise StudioError('임시 피봇의 대상 본이 없습니다.')
    frames = list(frames)
    writable = pose.writable_channels(arm, names, fcurve.TRANSFORMS)
    for name in names:
        pb = arm.pose.bones[name]
        if not writable[name] or pb.is_in_ik_chain or any(
                not c.mute and c.influence > 0 and not c.name.startswith(temp.PREFIX) for c in pb.constraints):
            raise StudioError('피봇을 굽기 전에 대상의 잠금·컨스트레인트를 확인하세요: ' + name)
    if not any(active_mode_controls(arm, names, None if len(frames) == 1 and frame ==
               context.scene.frame_current_final else frame)[0] for frame in frames):
        raise StudioError('구간에 현재 모드의 피봇 대상 컨트롤이 없습니다.')
    # 실패할 수 있는 편집 가능 검사를 정리 콜백 이전에 완료한다.
    fcurve.ensure_editable(arm.animation_data.action if arm.animation_data else None)
    return bake.bake(arm, names, frames, after_measure=lambda: remove_pivot(context), preserve_undo=True)


def delete_pivot(context):
    state = pivot_state(context)
    if state is None:
        raise StudioError('임시 피봇이 없습니다.')
    pivot, arm, names = state
    original = json.loads(pivot['anim_studio_original'])
    remove_pivot(context)
    # 생성한 프레임에서는 생성 전 미기록 자세까지, 다른 프레임에서는 원래 애니 유지.
    if abs(original['frame'] - context.scene.frame_current_final) < 1e-5:
        from .snapshot import apply
        apply(arm, original, writable=pose.writable_channels(arm, names, fcurve.TRANSFORMS))
    else:
        context.view_layer.update()
    return len(names)


def align_selection(context):
    active_object = context.active_object
    if active_object is not None and active_object.type != 'ARMATURE':
        arms = [obj for obj in context.selected_objects if obj.type == 'ARMATURE' and obj.mode == 'POSE']
        if len(arms) != 1:
            raise StudioError('포즈 모드 아마추어 하나와 활성 Target 오브젝트를 선택하세요.')
        arm = arms[0]
        names = [pb.name for pb in arm.pose.bones if pb.select]
        if not names:
            raise StudioError('Source 본을 선택하세요.')
        return {'arm': arm.name, 'sources': names, 'target_object': active_object.name, 'target_bone': ''}
    arm, names = pose.selected(context)
    active = context.active_pose_bone
    if active is None or active.name not in names:
        raise StudioError('활성 본을 정렬 대상으로 지정하세요.')
    sources = [n for n in names if n != active.name]
    if not sources:
        raise StudioError('Source 본과 활성 Target 본을 함께 선택하세요.')
    return {'arm': arm.name, 'sources': sources, 'target_object': arm.name, 'target_bone': active.name}


def align_state(context):
    text = context.window_manager.anim_studio_align
    if not text:
        raise StudioError('Set from Selection으로 Source와 Target을 지정하세요.')
    state = json.loads(text)
    arm = context.scene.objects.get(state['arm'])
    target = context.scene.objects.get(state['target_object'])
    if arm is None or arm.type != 'ARMATURE' or target is None:
        raise StudioError('정렬 대상이 없습니다. 다시 지정하세요.')
    if any(n not in arm.pose.bones for n in state['sources']):
        raise StudioError('Source 본이 없습니다. 다시 지정하세요.')
    name = state['target_bone']
    if name and (target.type != 'ARMATURE' or name not in target.pose.bones):
        raise StudioError('Target 본이 없습니다. 다시 지정하세요.')
    return arm, state['sources'], target, name


def target_matrix(target, bone):
    return target.matrix_world @ target.pose.bones[bone].matrix if bone else target.matrix_world.copy()
