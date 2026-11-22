# SPDX-License-Identifier: GPL-3.0-or-later
import json
from collections import Counter
from contextlib import contextmanager

import bpy


def active_armature(context):
    obj = context.active_object
    return obj if obj is not None and obj.type == 'ARMATURE' else None


def is_rig_studio(arm):
    return arm is not None and "rig_studio_info" in arm.data


def bone_meta(pbone):
    if "rig_studio" not in pbone.bone:
        return None
    return json.loads(pbone.bone["rig_studio"])


def control_bones(arm):
    if is_rig_studio(arm):
        return [pb for pb in arm.pose.bones
                if (meta := bone_meta(pb)) is not None and meta.get("type") == "CTRL"]
    return [pb for pb in arm.pose.bones if not pb.hide
            and (not pb.bone.collections
                 or any(c.is_visible_effectively for c in pb.bone.collections))]


def active_mode_controls(arm, bones, frame=None):
    """이름 목록을 현재 FK/IK 모드로 거른다. 명시한 씬 프레임은 커브로 읽는다."""
    names = list(dict.fromkeys(bones))
    if not is_rig_studio(arm):
        return names, []
    from . import fcurve
    limbs = json.loads(arm.data['rig_studio_info']).get('limbs', [])
    cb = fcurve.channelbag(arm, create=False)
    modes = {}
    for limb in limbs:
        ik = arm.pose.bones[limb['names']['ik']]
        value = ik['IK']
        if frame is not None and cb is not None:
            path = ik.path_from_id() + '["IK"]'
            curve = cb.fcurves.find(path, index=0)
            if curve is not None:
                value = curve.evaluate(fcurve.action_frame(arm, frame))
        modes[(limb['part'], limb['side'])] = value > 0.5
    active, skipped = [], []
    for name in names:
        meta = bone_meta(arm.pose.bones[name])
        mode = modes.get((meta.get('part'), meta.get('side'))) if meta else None
        role = meta.get('role') if meta else None
        reason = None
        if mode is True and role in {'up_fk', 'lo_fk', 'end_fk'}:
            reason = 'IK 모드라 FK 컨트롤 제외'
        elif mode is False and role in {'ik', 'pole'}:
            reason = 'FK 모드라 IK 컨트롤 제외'
        if reason:
            skipped.append((name, reason))
        else:
            active.append(name)
    return active, skipped


def mode_skip_message(skipped):
    counts = Counter(reason for _name, reason in set(skipped) if reason.endswith('컨트롤 제외'))
    return ' · '.join(reason.replace('컨트롤 제외', f'컨트롤 {count}개 제외')
                      for reason, count in sorted(counts.items()))


def frame_range(context):
    scene = context.scene
    if scene.use_preview_range:
        return scene.frame_preview_start, scene.frame_preview_end, "미리보기 범위"
    return scene.frame_start, scene.frame_end, "씬 범위"


def bone_depth(pbone):
    return len(pbone.parent_recursive)


def ordered_bones(arm, bones):
    return sorted((arm.pose.bones[name] for name in dict.fromkeys(bones)),
                  key=lambda pb: (bone_depth(pb), pb.name))


@contextmanager
def busy():
    namespace = bpy.app.driver_namespace
    nested = "anim_studio_busy" in namespace
    previous = namespace.get("anim_studio_busy")
    namespace["anim_studio_busy"] = True
    try:
        yield
    finally:
        if nested:
            namespace["anim_studio_busy"] = previous
        else:
            namespace.pop("anim_studio_busy", None)
