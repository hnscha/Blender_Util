# SPDX-License-Identifier: GPL-3.0-or-later
from .errors import StudioError
from dataclasses import dataclass
from time import perf_counter
from math import floor, isfinite

import bpy
from mathutils import Euler, Quaternion

from . import fcurve, snapshot, temp
from .context import active_mode_controls, busy, mode_skip_message, ordered_bones


@dataclass
class BakeResult:
    baked: list
    skipped: list
    frames: int
    seconds: float

    @property
    def mode_summary(self):
        return mode_skip_message(self.skipped)


def _rotation_reference(pb, cb, frame):
    frame = fcurve.action_frame(pb.id_data, frame)
    values = []
    for path, index, value in fcurve.transform_channels(pb, {'rotation'}):
        curve = cb.fcurves.find(path, index=index) if cb else None
        values.append(curve.evaluate(frame) if curve is not None else value)
    if pb.rotation_mode == 'QUATERNION':
        return Quaternion(values)
    if pb.rotation_mode == 'AXIS_ANGLE':
        return Quaternion(values[1:], values[0])
    return Euler(values, pb.rotation_mode)


def _continuous_rotation(pb, previous):
    if pb.rotation_mode == 'QUATERNION':
        rotation = pb.rotation_quaternion.copy()
        if rotation.dot(previous) < 0:
            rotation.negate()
        return 'rotation_quaternion', rotation, rotation
    if pb.rotation_mode == 'AXIS_ANGLE':
        angle, *axis = pb.rotation_axis_angle
        rotation = Quaternion(axis, angle)
        if rotation.dot(previous) < 0:
            rotation.negate()
        axis, angle = rotation.to_axis_angle()
        return 'rotation_axis_angle', (angle, *axis), rotation
    rotation = pb.rotation_euler.copy()
    rotation.make_compatible(previous)
    return 'rotation_euler', rotation, rotation


def bake(arm, bones, frames, target_fn=None, channels=fcurve.TRANSFORMS, after_measure=None,
         *, existing_properties_only=False, preserve_undo=False):
    started = perf_counter()
    frames = list(frames)
    if not frames or any(not isinstance(frame, (int, float)) or not isfinite(frame) for frame in frames):
        raise StudioError("베이크 프레임은 비어 있지 않은 유한한 숫자 목록이어야 합니다.")
    if any(a >= b for a, b in zip(frames, frames[1:])):
        raise StudioError("베이크 프레임은 중복 없이 오름차순이어야 합니다.")
    if not channels or not set(channels) <= fcurve.TRANSFORMS:
        raise StudioError("베이크 채널은 location, rotation, scale 중에서 선택해야 합니다.")
    fcurve.ensure_editable(arm.animation_data.action if arm.animation_data else None)
    targets, skipped, writable = [], [], {}
    for pb in ordered_bones(arm, bones):
        writable[pb.name] = set()
        for path, index, _value in fcurve.transform_channels(pb, channels):
            reason = fcurve.channel_skip_reason(arm, path, index)
            if reason:
                skipped.append((pb.name, f"{path.rsplit('.', 1)[-1]}[{index}]: {reason}"))
            else:
                writable[pb.name].add((path, index))
        if writable[pb.name]:
            targets.append(pb)
    if not targets:
        return BakeResult([], skipped, len(frames), perf_counter() - started)

    scene = bpy.context.scene
    original_frame, original_subframe = scene.frame_current, scene.frame_subframe
    single_current = len(frames) == 1 and frames[0] == scene.frame_current_final
    names = [pb.name for pb in targets]
    original = {pb.name: [(path.rsplit('.', 1)[-1], index, value)
                         for path, index, value in fcurve.transform_channels(pb)] for pb in targets}
    original_props = {pb.name: snapshot.numeric_properties(pb) for pb in targets}
    cb = fcurve.channelbag(arm, create=False)
    previous = {pb.name: _rotation_reference(pb, cb, frames[0] - 1) for pb in targets}
    measured, samples, baked = [], {}, set()
    custom_properties = {}
    completed = False
    with busy():
        try:
            # 현재 한 장은 frame_set 없이 아직 키를 찍지 않은 자세를 측정한다.
            for frame in frames:
                if not single_current:
                    scene.frame_set(floor(frame), subframe=frame - floor(frame))
                active, excluded = active_mode_controls(arm, names, None if single_current else frame)
                skipped.extend(entry for entry in excluded if entry not in skipped)
                frame_names = []
                for name in active:
                    pb = arm.pose.bones[name]
                    constraints = [c.name for c in pb.constraints if not c.mute and c.influence > 0
                                   and not c.name.startswith(temp.PREFIX)]
                    reason = ('IK 체인' if pb.is_in_ik_chain else
                              '활성 컨스트레인트: ' + ', '.join(constraints) if constraints else None)
                    if reason:
                        if (name, reason) not in skipped:
                            skipped.append((name, reason))
                    else:
                        frame_names.append(name)
                result = (target_fn(frame) if target_fn is not None else
                          {name: arm.matrix_world @ arm.pose.bones[name].matrix for name in frame_names})
                frame_names = [name for name in frame_names if name in result]
                properties = {}
                if isinstance(result, snapshot.PoseTargets):
                    skipped.extend(entry for entry in result.skipped if entry not in skipped)
                    for name in frame_names:
                        properties[name] = {}
                        for prop, value in result.custom_properties.get(name, {}).items():
                            path = arm.pose.bones[name].path_from_id() + '["' + bpy.utils.escape_identifier(prop) + '"]'
                            reason = fcurve.channel_skip_reason(arm, path, 0)
                            if reason:
                                entry = (name, f"{prop}: {reason}")
                                if entry not in skipped:
                                    skipped.append(entry)
                            else:
                                properties[name][prop] = value
                measured.append(snapshot.PoseTargets(
                    {name: result[name].copy() for name in frame_names}, properties,
                    space=result.space if isinstance(result, snapshot.PoseTargets) else 'WORLD'))
            if after_measure is not None:
                after_measure()
            else:
                temp.remove_constraints(arm, names)

            last_sample = {}
            for frame_index, (frame, pose) in enumerate(zip(frames, measured)):
                if not single_current:
                    scene.frame_set(floor(frame), subframe=frame - floor(frame))
                # 각 프레임의 원래 미기록 성분을 복원한 뒤 깊이별로 갱신한다.
                snapshot.apply(arm, pose, writable=writable, frame=None if single_current else frame)
                for name in pose:
                    pb = arm.pose.bones[name]
                    if last_sample.get(name, -1) != frame_index - 1:
                        previous[name] = _rotation_reference(pb, cb, frame - 1)
                    last_sample[name] = frame_index
                    prop, rotation, previous[pb.name] = _continuous_rotation(pb, previous[pb.name])
                    for channel, vector in (('location', pb.location), (prop, rotation), ('scale', pb.scale)):
                        path = pb.path_from_id(channel)
                        for index, value in enumerate(vector):
                            if (path, index) in writable[pb.name]:
                                samples.setdefault((pb.name, path, index), []).append((frame_index, frame, value))
                    for prop, value in pose.custom_properties.get(pb.name, {}).items():
                        path = pb.path_from_id() + '["' + bpy.utils.escape_identifier(prop) + '"]'
                        if existing_properties_only and (cb is None or cb.fcurves.find(path, index=0) is None):
                            continue
                        samples.setdefault((pb.name, path, 0), []).append((frame_index, frame, value))
                        custom_properties[(pb.name, path, 0)] = (pb, prop)
            if samples:
                cb = fcurve.channelbag(arm)
                owners = fcurve._owners(cb)
                for (name, path, index), entries in samples.items():
                    # 비활성 프레임 사이의 기존 키도 보존하도록 연속 대상 구간별 기록.
                    runs = []
                    for entry in entries:
                        if not runs or entry[0] != runs[-1][-1][0] + 1:
                            runs.append([])
                        runs[-1].append(entry)
                    for run in runs:
                        rejected = (fcurve.write_current_key(
                            arm, path, index, run[0][2], name, cb=cb, owners=owners,
                            custom_property=custom_properties.get((name, path, index))) if single_current else
                            fcurve.write_keys(cb, path, index, [e[1] for e in run], [e[2] for e in run],
                                              name, owners=owners, time_owner=arm,
                                              native_owner=arm if preserve_undo else None,
                                              custom_property=custom_properties.get((name, path, index))))
                        if rejected:
                            skipped.extend((name, reason) for _owner, reason in rejected)
                        else:
                            baked.add(name)
            completed = True
        finally:
            if not single_current:
                scene.frame_set(original_frame, subframe=original_subframe)
            if not single_current or not completed:
                restore, _excluded = active_mode_controls(arm, names)
                for pb in targets:
                    if pb.name not in restore:
                        continue
                    for prop, index, value in original[pb.name]:
                        curve = cb.fcurves.find(pb.path_from_id(prop), index=index) if cb else None
                        if not completed or curve is None:
                            getattr(pb, prop)[index] = value
                    if not completed:
                        for key in set(snapshot.numeric_properties(pb)) - original_props[pb.name].keys():
                            del pb[key]
                    for key, value in original_props[pb.name].items():
                        path = pb.path_from_id() + '["' + bpy.utils.escape_identifier(key) + '"]'
                        if not completed or cb is None or cb.fcurves.find(path, index=0) is None:
                            pb[key] = value
            arm.update_tag(refresh={'OBJECT'})
            bpy.context.view_layer.update()
    return BakeResult([name for name in names if name in baked], skipped,
                      len(frames), perf_counter() - started)
