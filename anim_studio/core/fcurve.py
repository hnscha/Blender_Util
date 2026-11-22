# SPDX-License-Identifier: GPL-3.0-or-later
from .errors import StudioError
import math
from contextlib import contextmanager
from array import array
from bisect import bisect_left, bisect_right

import bpy
from bpy_extras.anim_utils import (
    action_ensure_channelbag_for_slot,
    action_get_channelbag_for_slot,
)

TRANSFORMS = frozenset({'location', 'rotation', 'scale'})


@contextmanager
def preserve_current_pose(objects):
    """키 삭제의 재평가 뒤 같은 액션 사용자의 미기록 자세를 복원한다."""
    objects = set(objects)
    actions = {obj.animation_data.action for obj in objects
               if obj.animation_data and obj.animation_data.action}
    saved = []
    for obj in bpy.data.objects:
        if obj not in objects and (not obj.animation_data or obj.animation_data.action not in actions):
            continue
        owners = [obj, *obj.pose.bones] if obj.type == 'ARMATURE' else [obj]
        for owner in owners:
            channels = [(path.rsplit('.', 1)[-1], index, value)
                        for path, index, value in transform_channels(owner)]
            properties = {key: value for key, value in owner.items()
                          if isinstance(value, (int, float, bool))}
            saved.append((owner, channels, properties))
    try:
        yield
    finally:
        bpy.context.view_layer.update()
        # 성분을 직접 복원하여 쿼터니언 부호와 Euler 회전 횟수도 유지한다.
        for owner, channels, properties in saved:
            for prop, index, value in channels:
                getattr(owner, prop)[index] = value
            for key, value in properties.items():
                owner[key] = value


def ensure_editable(action):
    if action is not None and action.library is not None and action.override_library is None:
        raise StudioError("링크된 액션은 편집할 수 없습니다. 로컬 또는 오버라이드 액션을 사용해 주세요.")


def channelbag(arm, create=True):
    ad = arm.animation_data
    if not create:
        return (action_get_channelbag_for_slot(ad.action, ad.action_slot)
                if ad and ad.action and ad.action_slot else None)
    ensure_editable(ad.action if ad else None)
    ad = arm.animation_data_create()
    if ad.action is None:
        ad.action = bpy.data.actions.new(arm.name + "Action")
    if ad.action_slot is None:
        suitable = ad.action_suitable_slots
        ad.action_slot = (suitable[0] if suitable else
                          ad.action.slots.new(id_type='OBJECT', name=arm.name))
    return action_ensure_channelbag_for_slot(ad.action, ad.action_slot)


def transform_channels(owner, channels=TRANSFORMS):
    for channel in ('location', 'rotation', 'scale'):
        if channel not in channels:
            continue
        prop = channel
        if channel == 'rotation':
            prop = {'QUATERNION': 'rotation_quaternion',
                    'AXIS_ANGLE': 'rotation_axis_angle'}.get(owner.rotation_mode, 'rotation_euler')
        for index, value in enumerate(getattr(owner, prop)):
            yield owner.path_from_id(prop), index, value


def channel_skip_reason(obj, data_path, index):
    ad = obj.animation_data
    prop = data_path.rsplit('.', 1)[-1]
    coupled = prop in {'rotation_quaternion', 'rotation_axis_angle'}
    indices = range(4) if coupled else (index,)
    if ad and any(ad.drivers.find(data_path, index=i) is not None for i in indices):
        return "드라이버 채널"
    if prop not in {'location', 'rotation_euler', 'rotation_quaternion',
                    'rotation_axis_angle', 'scale'}:
        return None
    try:
        owner = obj.path_resolve(data_path.rsplit('.', 1)[0]) if '.' in data_path else obj
    except ValueError:
        # 삭제된 본의 커브에는 잠금 소유자가 없다. 드라이버 판정은 위에서 끝냈다.
        return None
    if prop == 'location':
        locked = owner.lock_location[index]
    elif prop == 'scale':
        locked = owner.lock_scale[index]
    elif prop == 'rotation_euler':
        locked = owner.lock_rotation[index]
    else:
        # 쿼터니언·축각은 일부만 쓰면 회전 자체가 변하므로 전체를 보호한다.
        locked = any(owner.lock_rotation) or owner.lock_rotation_w
    return "잠긴 채널" if locked else None


def _owners(cb):
    return [obj for obj in bpy.data.objects
            if obj.animation_data and obj.animation_data.action == cb.id_data
            and obj.animation_data.action_slot
            and obj.animation_data.action_slot.handle == cb.slot_handle]


def action_frame(obj, frame):
    """씬 시간을 현재 편집 중인 액션 시간으로 변환한다."""
    ad = obj.animation_data
    return ad.nla_tweak_strip_time_to_scene(frame, invert=True) if ad and ad.use_tweak_mode else frame


def _action_times(owners, frames, time_owner):
    if time_owner is not None:
        return [action_frame(time_owner, frame) for frame in frames]
    mapped = [tuple(action_frame(obj, frame) for frame in frames) for obj in owners]
    if mapped and any(times != mapped[0] for times in mapped[1:]):
        raise StudioError("공유 액션의 NLA 시간이 다릅니다. 편집할 오브젝트를 하나 선택해 주세요.")
    return list(mapped[0]) if mapped else list(frames)


def _skip(cb, data_path, index, owners=None):
    return [(obj.name, reason) for obj in (_owners(cb) if owners is None else owners)
            if (reason := channel_skip_reason(obj, data_path, index))]


def _coordinates(curve):
    coords = array('f', [0.0]) * (2 * len(curve.keyframe_points))
    curve.keyframe_points.foreach_get('co', coords)
    return coords


def _interval(coords, start, end):
    frames = coords[::2]
    return bisect_left(frames, start), bisect_right(frames, end)


def _remove_points(curve, start, end, *, outside=False):
    first, last = _interval(_coordinates(curve), start, end)
    count = len(curve.keyframe_points)
    indices = (*range(first), *range(last, count)) if outside else range(first, last)
    for index in reversed(indices):
        curve.keyframe_points.remove(curve.keyframe_points[index], fast=True)
    return len(indices)


def write_keys(cb, data_path, index, frames, values, group, *, owners=None, time_owner=None,
               native_owner=None, custom_property=None):
    """씬 프레임을 액션 시간으로 바꿔 벌크 기록한다. 반환값은 건너뛴 목록."""
    ensure_editable(cb.id_data)
    frames, values = list(frames), list(values)
    if not frames or len(frames) != len(values):
        raise StudioError("프레임과 값은 길이가 같은 비어 있지 않은 목록이어야 합니다.")
    if any(not math.isfinite(v) for v in (*frames, *values)):
        raise StudioError("프레임과 값에는 유한한 숫자만 사용할 수 있습니다.")
    if any(a >= b for a, b in zip(frames, frames[1:])):
        raise StudioError("프레임은 중복 없이 오름차순이어야 합니다.")
    owners = _owners(cb) if owners is None else owners
    scene_frames = frames
    frames = _action_times(owners, frames, time_owner)
    if any(a >= b for a, b in zip(frames, frames[1:])):
        raise StudioError("액션 시간이 증가하는 NLA 구간에서 베이크해 주세요.")
    skipped = _skip(cb, data_path, index, owners)
    if skipped:
        return skipped
    if native_owner is not None:
        _write_native_keys(native_owner, cb, data_path, index, scene_frames, frames, values, group,
                           custom_property)
        return []
    curve = cb.fcurves.ensure(data_path, index=index, group_name=group)
    coords = _coordinates(curve)
    first, last = _interval(coords, frames[0], frames[-1])
    for point_index in reversed(range(first, last)):
        curve.keyframe_points.remove(curve.keyframe_points[point_index], fast=True)
    coords = coords[:first * 2] + coords[last * 2:]
    old_count = len(curve.keyframe_points)
    coords.extend(v for pair in zip(frames, values) for v in pair)
    curve.keyframe_points.add(len(frames))
    curve.keyframe_points.foreach_set("co", coords)
    for point_index in range(old_count, old_count + len(frames)):
        point = curve.keyframe_points[point_index]
        point.interpolation = bpy.context.preferences.edit.keyframe_new_interpolation_type
        point.handle_left_type = point.handle_right_type = 'AUTO_CLAMPED'
    curve.update()
    for obj in owners:
        curve.update_autoflags(obj)
        obj.update_tag(refresh={'OBJECT'})
    return []


def _write_native_keys(obj, cb, path, index, scene_frames, action_frames, values, group, custom_property):
    """UI 구간 작업: Undo에서 미기록 자세를 재평가하지 않는 기본 키 API."""
    ad = obj.animation_data
    curve = cb.fcurves.find(path, index=index)
    old_frames = list(_coordinates(curve)[::2]) if curve is not None else []
    removed = [f for f in old_frames if action_frames[0] <= f <= action_frames[-1]]
    saved = custom_property[0][custom_property[1]] if custom_property else obj.path_resolve(path)[index]
    preferences = bpy.context.preferences.edit
    old_handle = preferences.keyframe_new_handle_type
    preferences.keyframe_new_handle_type = 'AUTO_CLAMPED'

    def put(frame, value):
        if custom_property:
            owner, prop = custom_property
            owner[prop] = value
        else:
            obj.path_resolve(path)[index] = value
        obj.keyframe_insert(path, index=-1 if custom_property else index, frame=frame, group=group)

    def scene_time(frame):
        return ad.nla_tweak_strip_time_to_scene(frame) if ad.use_tweak_mode else frame

    try:
        # 기본 삭제가 빈 F-커브를 제거하므로, 전체 교체 때는 임시 키 하나로
        # 기존 커브의 모디파이어·그룹·외삽 속성 수명을 유지한다.
        sentinel = None
        if old_frames and len(removed) == len(old_frames):
            upper = max(*old_frames, *action_frames)
            sentinel = scene_time(upper + max(1.0, abs(upper) * 1e-5))
            put(sentinel, saved)
        for frame in removed:
            obj.keyframe_delete(path, index=-1 if custom_property else index, frame=scene_time(frame))
        for frame, value in zip(scene_frames, values):
            put(frame, value)
        if sentinel is not None:
            obj.keyframe_delete(path, index=-1 if custom_property else index, frame=sentinel)
    finally:
        preferences.keyframe_new_handle_type = old_handle
        if custom_property:
            custom_property[0][custom_property[1]] = saved
        else:
            obj.path_resolve(path)[index] = saved


def delete_keys(cb, data_path, index, start, end, *, time_owner=None):
    """잠금·드라이버를 보존하고 삭제한 키 개수를 반환한다."""
    if cb is None or _skip(cb, data_path, index):
        return 0
    ensure_editable(cb.id_data)
    curve = cb.fcurves.find(data_path, index=index)
    if curve is None:
        return 0
    start, end = _action_times(_owners(cb), (start, end), time_owner)
    count = _remove_points(curve, min(start, end), max(start, end))
    _finish_delete(cb, curve)
    for obj in _owners(cb):
        obj.update_tag(refresh={'OBJECT'})
    return count


def _finish_delete(cb, curve):
    if not curve.keyframe_points:
        cb.fcurves.remove(curve)
    else:
        curve.update()


def delete_current_key(obj, data_path, index, *, cb, owners):
    """기본 삭제 API로 Undo 재평가 태그도 Blender와 일치시킨다."""
    if cb is None or _skip(cb, data_path, index, owners):
        return 0
    ensure_editable(cb.id_data)
    count = int(obj.keyframe_delete(data_path, index=index,
                                   frame=bpy.context.scene.frame_current_final))
    obj.update_tag(refresh={'OBJECT'})
    return count


def write_current_key(obj, data_path, index, value, group, *, cb=None, owners=None, custom_property=None):
    """현재 자세를 다시 평가하지 않는 단일 프레임 기록 경로."""
    cb = channelbag(obj) if cb is None else cb
    skipped = _skip(cb, data_path, index, owners)
    if skipped:
        return skipped
    # 베이크의 회전 연속성 보정값도 실제 채널에 반영한 후 기록한다.
    if custom_property is not None:
        owner, key = custom_property
        owner[key] = value
    else:
        obj.path_resolve(data_path)[index] = value
    obj.keyframe_insert(data_path, index=-1 if custom_property is not None else index,
                        frame=bpy.context.scene.frame_current_final,
                        group=group)
    return []


def delete_keys_outside(cb, start, end, *, data_paths=None, time_owner=None):
    """data_paths를 지정하면 선택한 본의 경로만 지운다."""
    plan = prepare_delete_outside(cb, start, end, data_paths=data_paths, time_owner=time_owner)
    with preserve_current_pose(_owners(cb) if cb is not None else []):
        return apply_delete_outside(plan)


def prepare_delete_outside(cb, start, end, *, data_paths=None, time_owner=None):
    """여러 오브젝트도 변경 전에 편집 가능 여부와 모든 skip 판정을 끝낸다."""
    if start > end:
        raise StudioError("프레임 범위의 시작은 끝보다 클 수 없습니다.")
    if cb is None:
        return cb, [], start, end
    ensure_editable(cb.id_data)
    owners = _owners(cb)
    start, end = _action_times(owners, (start, end), time_owner)
    curves = [curve for curve in cb.fcurves
              if (data_paths is None or curve.data_path in data_paths)
              and not _skip(cb, curve.data_path, curve.array_index, owners)]
    return cb, curves, min(start, end), max(start, end)


def apply_delete_outside(plan):
    cb, curves, start, end = plan
    if cb is None:
        return 0
    count = 0
    for curve in curves:
        count += _remove_points(curve, start, end, outside=True)
        _finish_delete(cb, curve)
    for obj in _owners(cb):
        obj.update_tag(refresh={'OBJECT'})
    return count
