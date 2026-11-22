# SPDX-License-Identifier: GPL-3.0-or-later
from .errors import StudioError
import json
from datetime import datetime, timezone

import bpy
from mathutils import Matrix

from .context import active_mode_controls, bone_depth, mode_skip_message, ordered_bones
from .fcurve import channel_skip_reason, transform_channels


class PoseTargets(dict):
    """공간을 명시한 행렬 매핑과 숫자·불 커스텀 속성."""
    def __init__(self, matrices, custom_properties, space='WORLD', skipped=()):
        super().__init__(matrices)
        self.custom_properties = custom_properties
        self.space = space
        self.skipped = list(skipped)

    @property
    def mode_summary(self):
        return mode_skip_message(self.skipped)


def numeric_properties(pbone):
    return {key: value for key, value in pbone.items()
            if isinstance(value, (int, float, bool))}


def _matrix_list(matrix):
    return [list(row) for row in matrix]


def capture(arm, bones, space='LOCAL'):
    if space not in {'LOCAL', 'WORLD'}:
        raise StudioError("스냅샷 공간은 LOCAL 또는 WORLD여야 합니다.")
    return {
        "armature": arm.name,
        "frame": bpy.context.scene.frame_current_final,
        "time": datetime.now(timezone.utc).isoformat(),
        "space": space,
        "bones": {
            name: {"matrix": _matrix_list(arm.pose.bones[name].matrix_basis if space == 'LOCAL'
                                         else arm.matrix_world @ arm.pose.bones[name].matrix),
                   "properties": numeric_properties(arm.pose.bones[name])}
            for name in dict.fromkeys(bones)
        },
    }


def apply(arm, snap, bones=None, space=None, *, writable=None, frame=None):
    """writable 지정 시 그 밖의 변환 성분은 적용 직전 값으로 보존한다."""
    if isinstance(snap, PoseTargets):
        records = {name: {"matrix": matrix,
                          "properties": snap.custom_properties.get(name, {})}
                   for name, matrix in snap.items()}
        stored_space = snap.space
    else:
        records, stored_space = snap["bones"], snap["space"]
    space = stored_space if space is None else space
    if space != stored_space or space not in {'LOCAL', 'WORLD'}:
        raise StudioError("스냅샷에 저장된 공간과 적용 공간이 다릅니다.")
    names, skipped = active_mode_controls(arm, records if bones is None else bones, frame)
    targets = ordered_bones(arm, names)
    # 모든 입력을 먼저 읽어 잘못된 이름·행렬 때문에 일부만 적용되지 않게 한다.
    matrices = {pb.name: Matrix(records[pb.name]["matrix"]) for pb in targets}
    preserved = ({pb.name: [(path.rsplit('.', 1)[-1], index, value)
                           for path, index, value in transform_channels(pb)
                           if (path, index) not in writable[pb.name]] for pb in targets}
                 if writable is not None else {})
    inverse = arm.matrix_world.inverted() if space == 'WORLD' else None
    # 속성이 부모/컨스트레인트 평가에 영향을 줄 수 있어 행렬보다 먼저 반영한다.
    for pb in targets:
        for name, value in records[pb.name]["properties"].items():
            path = pb.path_from_id() + '["' + bpy.utils.escape_identifier(name) + '"]'
            if channel_skip_reason(arm, path, 0) is None:
                pb[name] = value
    if any(records[pb.name]['properties'] for pb in targets):
        arm.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
    previous_depth = None
    for pb in targets:
        depth = bone_depth(pb)
        if space == 'WORLD' and previous_depth is not None and depth != previous_depth:
            bpy.context.view_layer.update()
        if space == 'LOCAL':
            pb.matrix_basis = matrices[pb.name]
        else:
            pb.matrix = inverse @ matrices[pb.name]
        for prop, index, value in preserved.get(pb.name, ()):
            getattr(pb, prop)[index] = value
        previous_depth = depth
    arm.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()
    return skipped


def rest_record(arm):
    info = arm.data.get("rig_studio_info")
    if isinstance(info, str):
        info = json.loads(info)
    return {"bones": {bone.name: _matrix_list(bone.matrix_local) for bone in arm.data.bones},
            "version": info.get("version") if info is not None else None}
