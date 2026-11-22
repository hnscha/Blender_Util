# SPDX-License-Identifier: GPL-3.0-or-later
"""
Strict Lock — 자물쇠가 걸린 변환 채널을 정말로 못 바꾸게 한다.

Blender 의 채널 잠금(자물쇠)은 뷰포트 변환(G/R/S, 기즈모)만 막고,
숫자 칸 끌기·직접 입력·여러 칸 드래그·커브 에디터 편집·키 찍기는 막지 않는다.
이 애드온은 잠긴 채널에 대해 아래를 전부 막는다.

  1. 드래그 이동 (뷰포트 변환, 여러 칸 동시 드래그 포함)
  2. 채널 값 입력 (숫자 칸 끌기·직접 입력·파이썬)
  3. 커브 에디터 조절 (잠긴 채널의 F-Curve 를 잠그고, 키·핸들이 바뀌면 되돌림)
     + 잠긴 채널에 새로 찍힌 키(자동 키·키잉 세트)도 지운다.

방법: 잠긴 순간의 값(과 F-Curve 키)을 기억해 두고, 장면이 바뀔 때마다 비교해서 바뀌었으면 되돌린다.
자물쇠를 풀면 그 채널은 자유롭고, 다시 잠그면 그때 값을 새로 기억한다.
"""

import bpy
from bpy_extras import anim_utils

# (소유자 키, data_path 끝부분, index) -> {"value": float, "keys": [...] | None}
_snap = {}
_busy = False
_msg_timer_on = False
_recent = []          # 최근에 막은 기록 (최대 20개)


# -----------------------------------------------------------------------------
# 잠긴 채널 찾기

def _channels(owner, include_unlocked=True):
    """(data_path 끝부분, index, 잠김 여부) 목록. 회전은 회전 모드에 맞는 속성."""
    # RNA 배열을 축마다 다시 찾지 않는다. 콜백 간 캐시는 두지 않아 잠금 변경이
    # 즉시 반영되며, 새 소유자는 잠긴 채널만 만들어도 된다.
    location = owner.lock_location[:]
    scale = owner.lock_scale[:]
    rotation = owner.lock_rotation[:]
    mode = owner.rotation_mode
    four_d = mode in {'QUATERNION', 'AXIS_ANGLE'}
    locked_w = four_d and owner.lock_rotations_4d and owner.lock_rotation_w
    if not include_unlocked and not (any(location) or any(scale) or any(rotation) or locked_w):
        return ()
    out = []
    for i in range(3):
        if include_unlocked or location[i]:
            out.append(("location", i, location[i]))
        if include_unlocked or scale[i]:
            out.append(("scale", i, scale[i]))
    path = {'QUATERNION': "rotation_quaternion", 'AXIS_ANGLE': "rotation_axis_angle"}.get(mode, "rotation_euler")
    if four_d and (include_unlocked or locked_w):
        out.append((path, 0, locked_w))
    offset = 1 if four_d else 0
    for i in range(3):
        if include_unlocked or rotation[i]:
            out.append((path, i + offset, rotation[i]))
    return out


def _owners(only_ids=None):
    """(소유자 키, 소유자, ID(애니메이션 데이터를 가진 오브젝트), data_path 앞부분) 목록."""
    scene = bpy.context.scene
    if scene is None:
        return
    for obj in scene.objects:
        if only_ids is not None and obj.name not in only_ids:
            continue
        yield ("OB", obj.name), obj, obj, ""
        if obj.type == 'ARMATURE' and obj.pose is not None:
            for pb in obj.pose.bones:
                yield ("PB", obj.name, pb.name), pb, obj, 'pose.bones["{:s}"].'.format(pb.name)


# -----------------------------------------------------------------------------
# F-Curve

def _channelbag(id_obj):
    ad = id_obj.animation_data
    if ad is None or ad.action is None:
        return None
    try:
        return anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    except Exception:
        return None


def _fcurve(id_obj, full_path, index):
    cb = _channelbag(id_obj)
    if cb is None:
        return None
    return cb.fcurves.find(full_path, index=index)


def _keys_of(fc):
    return [
        (tuple(k.co), tuple(k.handle_left), tuple(k.handle_right),
         k.interpolation, k.handle_left_type, k.handle_right_type, k.easing)
        for k in fc.keyframe_points
    ]


def _restore_keys(fc, keys):
    pts = fc.keyframe_points
    if len(pts) != len(keys):
        while len(pts):
            pts.remove(pts[0], fast=True)
        pts.add(len(keys))
    for k, (co, hl, hr, interp, hlt, hrt, ease) in zip(pts, keys):
        k.co = co
        k.interpolation = interp
        k.handle_left_type = hlt
        k.handle_right_type = hrt
        k.handle_left = hl
        k.handle_right = hr
        k.easing = ease
    fc.update()


# -----------------------------------------------------------------------------
# 기억·검사

def _prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _enabled():
    p = _prefs()
    return p is not None and p.enabled


def _report(text):
    global _msg_timer_on
    _recent.insert(0, text)
    del _recent[20:]
    for win in bpy.context.window_manager.windows:
        try:
            win.workspace.status_text_set("Strict Lock: " + text)
        except Exception:
            pass
    if not _msg_timer_on:
        _msg_timer_on = True
        bpy.app.timers.register(_clear_status, first_interval=2.5)


def _clear_status():
    global _msg_timer_on
    _msg_timer_on = False
    for win in bpy.context.window_manager.windows:
        try:
            win.workspace.status_text_set(None)
        except Exception:
            pass
    return None


def _capture(key, owner, id_obj, prefix, path, index):
    fc = _fcurve(id_obj, prefix + path, index)
    entry = {"value": getattr(owner, path)[index], "keys": _keys_of(fc) if fc else None}
    _snap[(key, path, index)] = entry
    if fc is not None and _prefs().lock_fcurves:
        fc.lock = True
    return entry


def recapture_all():
    """지금 잠긴 채널의 값·키를 모두 다시 기억한다."""
    _snap.clear()
    if not _enabled():
        return
    for key, owner, id_obj, prefix in _owners():
        for path, index, locked in _channels(owner, include_unlocked=False):
            if locked:
                _capture(key, owner, id_obj, prefix, path, index)


def _anim_lookup(id_obj):
    """(F-Curve 사전, 드라이버 집합, NLA 없음 여부) — 오브젝트당 한 번만 만든다."""
    cb = _channelbag(id_obj)
    fcs = {(f.data_path, f.array_index): f for f in cb.fcurves} if cb is not None else {}
    ad = id_obj.animation_data
    drv = {(d.data_path, d.array_index) for d in ad.drivers} if ad is not None else set()
    # NLA 가 섞이면 커브 값 ≠ 최종 값이라 값 비교는 하지 않는다 (커브 보호는 그대로).
    plain = ad is not None and len(ad.nla_tracks) == 0 and ad.action_influence >= 1.0
    return fcs, drv, plain


def _enforce(only_ids=None):
    """잠긴 채널이 바뀌었으면 되돌린다. 바뀐 게 있으면 True.
    only_ids: 이번에 바뀐 오브젝트 이름 집합 (None 이면 전부 검사)."""
    changed = False
    seen = set()
    prefs = _prefs()
    remove_new_keys = prefs.remove_new_keys
    # 이전 snapshot이 있는 소유자는 모두 확인해 unlock 시 F-Curve 잠금을 푼다.
    # 매번 새로 만들므로 Undo/load 및 driver/rotation mode 변경에 stale cache가 없다.
    tracked_owners = {sk[0] for sk in _snap}
    lookups = {}
    frame = bpy.context.scene.frame_current + bpy.context.scene.frame_subframe
    for key, owner, id_obj, prefix in _owners(only_ids):
        channels = _channels(owner, include_unlocked=key in tracked_owners)
        if not channels:
            continue
        lk = lookups.get(id_obj.name)
        if lk is None:
            lk = lookups[id_obj.name] = _anim_lookup(id_obj)
        fcs, drv, plain = lk
        for path, index, locked in channels:
            sk = (key, path, index)
            if not locked:
                if sk in _snap:
                    # 자물쇠를 풀었다: 자유. F-Curve 잠금도 풀어 준다.
                    fc = fcs.get((prefix + path, index))
                    if fc is not None:
                        fc.lock = False
                    del _snap[sk]
                continue
            seen.add(sk)
            entry = _snap.get(sk)
            if entry is None:
                # 새로 잠갔다: 지금 값을 기억한다.
                _capture(key, owner, id_obj, prefix, path, index)
                continue
            full = prefix + path
            if (full, index) in drv:
                continue
            fc = fcs.get((full, index))
            name = key[-1]
            if entry["keys"] is None:
                if fc is not None:
                    if remove_new_keys:
                        # 키가 없던 잠긴 채널에 키가 생겼다 (자동 키 등): 지운다.
                        _channelbag(id_obj).fcurves.remove(fc)
                        _report("{:s} {:s}[{:d}] is locked — key removed".format(name, path, index))
                        changed = True
                    else:
                        entry["keys"] = _keys_of(fc)
                        continue
                arr = getattr(owner, path)
                if abs(arr[index] - entry["value"]) > 1e-7:
                    arr[index] = entry["value"]
                    _report("{:s} {:s}[{:d}] is locked".format(name, path, index))
                    changed = True
            else:
                # 키가 있는 잠긴 채널: 커브(키·핸들)를 지킨다. 값은 커브가 정하므로 비교하지 않는다.
                if fc is None:
                    cb = anim_utils.action_ensure_channelbag_for_slot(
                        id_obj.animation_data.action, id_obj.animation_data.action_slot)
                    fc = cb.fcurves.new(full, index=index)
                    _restore_keys(fc, entry["keys"])
                    _report("{:s} {:s}[{:d}] is locked — curve restored".format(name, path, index))
                    changed = True
                elif _keys_of(fc) != entry["keys"]:
                    _restore_keys(fc, entry["keys"])
                    _report("{:s} {:s}[{:d}] is locked — curve edit undone".format(name, path, index))
                    changed = True
                if prefs.lock_fcurves and not fc.lock:
                    fc.lock = True
                # 키가 있어도 칸에서 값만 바꾸면 (키 안 찍고) 화면이 바뀐다: 커브 값으로 되돌린다.
                if plain and not fc.mute:
                    arr = getattr(owner, path)
                    want = fc.evaluate(frame)
                    if abs(arr[index] - want) > 1e-5:
                        arr[index] = want
                        _report("{:s} {:s}[{:d}] is locked".format(name, path, index))
                        changed = True
    # 사라진 소유자 정리 (전부 검사했을 때만)
    if only_ids is None:
        for sk in [k for k in _snap if k not in seen]:
            del _snap[sk]
    return changed


@bpy.app.handlers.persistent
def _on_depsgraph(_scene, depsgraph):
    global _busy
    if _busy or not _enabled():
        return
    # 이번에 바뀐 오브젝트만 검사한다. 액션(커브)이 바뀌면 그 액션을 쓰는 오브젝트도.
    ids = set()
    actions = set()
    for upd in depsgraph.updates:
        idb = upd.id.original
        if isinstance(idb, bpy.types.Object):
            ids.add(idb.name)
        elif isinstance(idb, bpy.types.Action):
            actions.add(idb.name)
        elif isinstance(idb, bpy.types.Scene):
            ids = None
            break
    if ids is not None and actions:
        for obj in bpy.context.scene.objects:
            ad = obj.animation_data
            if ad is not None and ad.action is not None and ad.action.name in actions:
                ids.add(obj.name)
    if ids is not None and not ids:
        return
    _busy = True
    try:
        _enforce(ids)
    finally:
        _busy = False


@bpy.app.handlers.persistent
def _on_load(_dummy):
    bpy.app.timers.register(_delayed_capture, first_interval=0.1)


def _delayed_capture():
    recapture_all()
    return None


# -----------------------------------------------------------------------------
# UI

def _update_enabled(self, _context):
    if self.enabled:
        recapture_all()
    else:
        _unlock_all_fcurves()
        _snap.clear()


def _update_lock_fcurves(self, _context):
    if self.lock_fcurves:
        recapture_all()
    else:
        _unlock_all_fcurves()


def _unlock_all_fcurves():
    for sk in list(_snap):
        key, path, index = sk
        id_obj = bpy.data.objects.get(key[1])
        if id_obj is None:
            continue
        prefix = 'pose.bones["{:s}"].'.format(key[2]) if key[0] == "PB" else ""
        fc = _fcurve(id_obj, prefix + path, index)
        if fc is not None:
            fc.lock = False


class StrictLockPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    enabled: bpy.props.BoolProperty(
        name="Strict Lock",
        description="Locked transform channels can't be changed by dragging, typing or curve editing",
        default=True,
        update=_update_enabled,
    )
    lock_fcurves: bpy.props.BoolProperty(
        name="Lock Curves of Locked Channels",
        description="Lock the F-Curves of locked channels so they can't be edited in the Graph Editor / Dope Sheet",
        default=True,
        update=_update_lock_fcurves,
    )
    remove_new_keys: bpy.props.BoolProperty(
        name="Remove New Keys on Locked Channels",
        description="Delete keys that auto keying or keying sets add to a locked channel that had no keys",
        default=True,
    )

    def draw(self, context):
        _draw(self.layout, self)


def _draw(layout, prefs):
    layout.prop(prefs, "enabled", translate=False)
    col = layout.column()
    col.active = prefs.enabled
    col.prop(prefs, "lock_fcurves", translate=False)
    col.prop(prefs, "remove_new_keys", translate=False)
    col.operator("strict_lock.recapture", icon='FILE_REFRESH', translate=False)
    col.operator("strict_lock.clear_locked_keys", icon='TRASH', translate=False)
    col.label(text="Locked channels watched: {:d}".format(len(_snap)), translate=False)
    if _recent:
        box = layout.box()
        box.label(text="Recently blocked", translate=False)
        for line in _recent[:6]:
            box.label(text=line, translate=False)


class STRICTLOCK_OT_recapture(bpy.types.Operator):
    """Remember the current values of all locked channels as their locked values"""
    bl_idname = "strict_lock.recapture"
    bl_label = "Remember Locked Values Now"
    bl_options = {'REGISTER'}

    def execute(self, _context):
        recapture_all()
        self.report({'INFO'}, "Strict Lock: {:d} locked channels remembered".format(len(_snap)))
        return {'FINISHED'}


class STRICTLOCK_OT_clear_locked_keys(bpy.types.Operator):
    """Delete all keys on locked channels of the selected objects / bones"""
    bl_idname = "strict_lock.clear_locked_keys"
    bl_label = "Delete Keys on Locked Channels"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        global _busy
        _busy = True
        removed = 0
        try:
            targets = []
            for obj in context.selected_objects:
                targets.append((obj, obj, ""))
            for pb in (context.selected_pose_bones or []):
                targets.append((pb, pb.id_data, 'pose.bones["{:s}"].'.format(pb.name)))
            for owner, id_obj, prefix in targets:
                cb = _channelbag(id_obj)
                if cb is None:
                    continue
                for path, index, locked in _channels(owner):
                    if not locked:
                        continue
                    fc = cb.fcurves.find(prefix + path, index=index)
                    if fc is not None:
                        cb.fcurves.remove(fc)
                        removed += 1
        finally:
            _busy = False
        recapture_all()
        self.report({'INFO'}, "Strict Lock: removed {:d} curves on locked channels".format(removed))
        return {'FINISHED'}


class VIEW3D_PT_strict_lock(bpy.types.Panel):
    bl_label = "Strict Lock"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Lock"

    def draw(self, _context):
        prefs = _prefs()
        if prefs is not None:
            _draw(self.layout, prefs)


_classes = (
    StrictLockPreferences,
    STRICTLOCK_OT_recapture,
    STRICTLOCK_OT_clear_locked_keys,
    VIEW3D_PT_strict_lock,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    # 등록 중에는 bpy.data 에 접근할 수 없어서 잠깐 뒤에 기억한다.
    bpy.app.timers.register(_delayed_capture, first_interval=0.1)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    try:
        _unlock_all_fcurves()
    except Exception:
        pass
    _snap.clear()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
