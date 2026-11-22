# SPDX-License-Identifier: GPL-3.0-or-later
"""포즈 도구 공통 측정·보간·키 기록. UI/오퍼레이터에는 계산을 두지 않는다."""
import json
from math import floor

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

from . import bake, fcurve, mirror, snapshot
from .context import (active_armature, active_mode_controls, bone_meta, busy,
                      frame_range, is_rig_studio, mode_skip_message)
from .errors import StudioError


def selected(context):
    arm = active_armature(context)
    if context.mode != 'POSE' or arm is None:
        raise StudioError('포즈 모드에서 본을 선택하세요.')
    names = [pb.name for pb in context.selected_pose_bones or () if pb.id_data == arm]
    if not names:
        raise StudioError('본을 선택하세요')
    if not arm.is_editable:
        raise StudioError('편집할 수 있는 아마추어를 선택하세요.')
    return arm, names


def targets(record, names=None):
    names = record['bones'] if names is None else names
    return snapshot.PoseTargets({name: Matrix(record['bones'][name]['matrix']) for name in names
                                 if name in record['bones']},
                                {name: record['bones'][name]['properties'].copy() for name in names
                                 if name in record['bones']}, space=record['space'])


def subset(pose, names):
    return snapshot.PoseTargets({n: pose[n].copy() for n in names if n in pose},
                                {n: pose.custom_properties.get(n, {}).copy() for n in names if n in pose},
                                space=pose.space, skipped=pose.skipped)


def current(arm, names, space='LOCAL'):
    return targets(snapshot.capture(arm, names, space))


def property_path(pb, name):
    return pb.path_from_id() + '["' + bpy.utils.escape_identifier(name) + '"]'


def protect_modes(arm, pose):
    """Default/Buffer 등도 현재 팔다리 모드를 암묵적으로 바꾸지 않는다."""
    pose = subset(pose, pose)
    if is_rig_studio(arm):
        for limb in json.loads(arm.data['rig_studio_info']).get('limbs', []):
            pose.custom_properties.get(limb['names']['ik'], {}).pop('IK', None)
    return pose


def writable_channels(arm, names, channels):
    cb = fcurve.channelbag(arm, create=False)
    owners = fcurve._owners(cb) if cb is not None else [arm]
    return {name: {(path, index) for path, index, _value in
                   fcurve.transform_channels(arm.pose.bones[name], channels)
                   if not any(fcurve.channel_skip_reason(owner, path, index) for owner in owners)}
            for name in names}


def apply_current(arm, pose, channels=fcurve.TRANSFORMS, *, key=True):
    fcurve.ensure_editable(arm.animation_data.action if arm.animation_data else None)
    names, skipped = active_mode_controls(arm, list(pose))
    skipped = list(dict.fromkeys([*pose.skipped, *skipped]))
    if not names:
        raise StudioError(mode_skip_message(skipped) or '적용할 본이 없습니다.')
    pose = protect_modes(arm, subset(pose, names))
    writable = writable_channels(arm, names, channels)
    if not any(writable.values()):
        raise StudioError('기록할 채널이 없습니다. 잠금·드라이버를 확인하세요.')
    cb = fcurve.channelbag(arm, create=False)
    custom_keys = {(n, p) for n in names for p in pose.custom_properties.get(n, {})
                   if cb is not None and cb.fcurves.find(property_path(arm.pose.bones[n], p), index=0)}
    with busy():
        snapshot.apply(arm, pose, writable=writable)
        if key:
            cb = fcurve.channelbag(arm)
            owners = fcurve._owners(cb)
            # 적용한 자세를 기준으로 공유 액션 사용자의 미기록 성분도 보존한다.
            with fcurve.preserve_current_pose([arm]):
                for n in names:
                    pb = arm.pose.bones[n]
                    for path, index, value in fcurve.transform_channels(pb, channels):
                        if (path, index) in writable[n]:
                            fcurve.write_current_key(arm, path, index, value, n, cb=cb, owners=owners)
                    for prop, value in pose.custom_properties.get(n, {}).items():
                        if (n, prop) in custom_keys:
                            fcurve.write_current_key(arm, property_path(pb, prop), 0, value, n,
                                                     cb=cb, owners=owners, custom_property=(pb, prop))
        arm.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
    return f'{len(names)} bones · F{bpy.context.scene.frame_current_final:g}' + (
        ' · ' + mode_skip_message(skipped) if mode_skip_message(skipped) else '')


def key_frames(arm, names, start=None, end=None):
    cb = fcurve.channelbag(arm, create=False)
    if cb is None:
        return []
    prefixes = tuple(arm.pose.bones[n].path_from_id() for n in names)
    ad = arm.animation_data
    frames = set()
    for curve in cb.fcurves:
        if not any(curve.data_path.startswith(p + '.') or curve.data_path.startswith(p + '[')
                   for p in prefixes):
            continue
        for frame in fcurve._coordinates(curve)[::2]:
            frame = ad.nla_tweak_strip_time_to_scene(frame) if ad.use_tweak_mode else frame
            if (start is None or start <= frame) and (end is None or frame <= end):
                frames.add(frame)
    return sorted(frames)


def neighbor_frame(arm, names, frame, direction):
    frames = key_frames(arm, names)
    candidates = [f for f in frames if f < frame - 1e-5] if direction == 'PREV' else [
        f for f in frames if f > frame + 1e-5]
    if not candidates:
        raise StudioError('이전 키가 없습니다.' if direction == 'PREV' else '다음 키가 없습니다.')
    return candidates[-1] if direction == 'PREV' else candidates[0]


def evaluate_local(arm, names, frame):
    cb = fcurve.channelbag(arm, create=False)
    time = fcurve.action_frame(arm, frame)
    matrices, properties = {}, {}
    for name in names:
        pb = arm.pose.bones[name]
        vectors = {}
        for path, index, value in fcurve.transform_channels(pb):
            prop = path.rsplit('.', 1)[-1]
            curve = cb.fcurves.find(path, index=index) if cb is not None else None
            vectors.setdefault(prop, []).append(curve.evaluate(time) if curve is not None else value)
        if pb.rotation_mode == 'QUATERNION':
            rotation = Quaternion(vectors['rotation_quaternion']).normalized()
        elif pb.rotation_mode == 'AXIS_ANGLE':
            angle, *axis = vectors['rotation_axis_angle']
            rotation = Quaternion(axis, angle)
        else:
            rotation = Euler(vectors['rotation_euler'], pb.rotation_mode).to_quaternion()
        matrices[name] = Matrix.LocRotScale(Vector(vectors['location']), rotation, Vector(vectors['scale']))
        properties[name] = {}
        for prop, value in snapshot.numeric_properties(pb).items():
            curve = cb.fcurves.find(property_path(pb, prop), index=0) if cb is not None else None
            properties[name][prop] = curve.evaluate(time) if curve is not None else value
    return snapshot.PoseTargets(matrices, properties, space='LOCAL')


def measure(arm, names, frames, space='WORLD'):
    """별도 프레임의 평가가 필요한 월드/루프 측정만. 현재 미기록 자세를 복원한다."""
    scene = bpy.context.scene
    original, subframe = scene.frame_current, scene.frame_subframe
    with busy(), fcurve.preserve_current_pose(list(scene.objects)):
        try:
            result = {}
            for frame in frames:
                scene.frame_set(floor(frame), subframe=frame - floor(frame))
                sample = current(arm, names, 'WORLD' if space == 'ARMATURE' else space)
                if space == 'ARMATURE':
                    inverse = arm.matrix_world.inverted()
                    sample = snapshot.PoseTargets({n: inverse @ m for n, m in sample.items()},
                                                  sample.custom_properties, space='ARMATURE')
                result[frame] = sample
            return result
        finally:
            scene.frame_set(original, subframe=subframe)


def mirror_saved(arm, pose, mode='SWAP', *, frame=None, source_frame=None):
    if pose.space != 'LOCAL':
        raise StudioError('미러에는 로컬 포즈 기록이 필요합니다.')
    return protect_modes(arm, mirror.mirror_matrices(arm, pose, mode, properties=pose.custom_properties,
                                                    frame=frame, source_frame=source_frame))


def blend(a, b, factor):
    matrices, properties = {}, {}
    for name in a.keys() & b.keys():
        al, ar, asc = a[name].decompose()
        bl, br, bsc = b[name].decompose()
        if ar.dot(br) < 0:
            br.negate()
        # mathutils.slerp는 [0, 1]만 받는다. 최단 상대 회전의 축각 배율은
        # 같은 구면 보간식을 음수/100% 초과 구간까지 연장한다.
        axis, angle = (ar.conjugated() @ br).to_axis_angle()
        rotation = (ar @ Quaternion(axis, angle * factor)).normalized()
        matrices[name] = Matrix.LocRotScale(al.lerp(bl, factor), rotation, asc.lerp(bsc, factor))
        properties[name] = {}
        for key, value in a.custom_properties.get(name, {}).items():
            end = b.custom_properties.get(name, {}).get(key, value)
            mixed = value + (end - value) * factor
            properties[name][key] = bool(mixed >= 0.5) if isinstance(value, bool) else (
                round(mixed) if isinstance(value, int) else mixed)
    return snapshot.PoseTargets(matrices, properties, space='LOCAL')


def tween_endpoints(arm, names, target, buffer=None):
    a = current(arm, names)
    if target == 'NEIGHBORS':
        frame = bpy.context.scene.frame_current_final
        before = neighbor_frame(arm, names, frame, 'PREV')
        after = neighbor_frame(arm, names, frame, 'NEXT')
        return evaluate_local(arm, names, before), evaluate_local(arm, names, after)
    if target == 'DEFAULT':
        properties = {}
        for name in names:
            pb = arm.pose.bones[name]
            properties[name] = {key: pb.id_properties_ui(key).as_dict().get('default', value)
                                for key, value in snapshot.numeric_properties(pb).items()}
        b = snapshot.PoseTargets({n: Matrix.Identity(4) for n in names}, properties, space='LOCAL')
    elif target == 'MIRROR':
        b = mirror.mirror_pose(arm, names)
    elif target == 'BUFFER':
        if buffer is None:
            raise StudioError('포즈 복사 기록을 선택하세요.')
        b = targets(buffer['LOCAL'], names)
        if not b:
            raise StudioError('선택한 본이 포즈 기록에 없습니다.')
    else:
        raise StudioError('지원하지 않는 트윈 목표입니다.')
    return subset(a, b), protect_modes(arm, b)


def bake_pose(arm, names, frames, target_fn):
    """기존 커브가 있는 커스텀 속성만 구간에 키를 쓴다."""
    def target(frame):
        return protect_modes(arm, target_fn(frame))
    return bake.bake(arm, names, frames, target_fn=target, existing_properties_only=True, preserve_undo=True)


def loop(arm, names, context, half='FIRST', single=False):
    start, end, _reason = frame_range(context)
    length = end - start
    if length <= 0 or length % 2:
        raise StudioError('루프 구간 길이(end − start)는 양의 짝수여야 합니다.')
    h = length / 2
    mapping = mirror.pairs(arm)
    names = sorted(set(names) | {mapping[n] for n in names})
    current_frame = context.scene.frame_current_final
    if single:
        if not start <= current_frame <= end:
            raise StudioError('현재 프레임은 루프 구간 안에 있어야 합니다.')
        source_frames = [current_frame]
        direction = 1 if current_frame < start + h else -1
        unkeyed = current(arm, names)
        unkeyed_positions = {n: arm.pose.bones[n].matrix.copy() for n in names}
    else:
        direction = 1 if half == 'FIRST' else -1
        lo, hi = (start, start + h) if direction == 1 else (start + h, end)
        source_frames = [f for f in key_frames(arm, names, lo, hi) if f < hi]
        if not source_frames:
            raise StudioError('원본 반주기에 키가 없습니다.')
    samples = measure(arm, names, sorted(set(source_frames) | {start, end}), 'LOCAL')
    if single:
        samples[current_frame] = unkeyed
    travel = [n for n in names if is_rig_studio(arm)
              and (bone_meta(arm.pose.bones[n]) or {}).get('role') in {'root', 'com'}]
    root = min(travel, key=lambda n: len(arm.pose.bones[n].parent_recursive)) if travel else None
    positions = measure(arm, [root], sorted(set(source_frames) | {start, end}), 'ARMATURE') if root else {}
    displacement = (positions[end][root].translation.y - positions[start][root].translation.y) if root else 0.0
    if root and single:
        positions[current_frame][root] = unkeyed_positions[root]
    poses, desired = {}, {}
    for source in source_frames:
        destination = source + direction * h
        poses[destination] = mirror_saved(arm, samples[source], frame=destination, source_frame=source)
        if root:
            point = positions[source][root].translation
            desired[destination] = Vector((-point.x, point.y + direction * displacement / 2, point.z))
    if not single:
        # 마지막 포즈는 첫 포즈로 닫되, 루트 진행량은 한 주기를 유지한다.
        poses[end] = subset(samples[start] if direction == 1 else poses.get(start, samples[start]), names)
        if root:
            point = positions[start][root].translation.copy()
            if direction == -1 and start in desired:
                point = desired[start].copy()
            point.y += displacement
            desired[end] = point

    def target(frame):
        pose = subset(poses[frame], poses[frame])
        if root and root in pose:
            pb = arm.pose.bones[root]
            parent_rest = pb.matrix @ pb.matrix_basis.inverted()
            pose[root].translation = parent_rest.inverted() @ desired[frame]
        return pose

    return bake_pose(arm, names, sorted(poses), target)


def range_message(context, result):
    start, end, reason = frame_range(context)
    return f'{len(result.baked)} bones · {start}–{end} ({reason})' + (
        ' · ' + result.mode_summary if result.mode_summary else '')
