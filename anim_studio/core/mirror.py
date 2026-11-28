# SPDX-License-Identifier: GPL-3.0-or-later
from .errors import StudioError
import re

import bpy
from mathutils import Matrix

from .context import active_mode_controls, bone_meta, is_rig_studio
from .snapshot import PoseTargets, numeric_properties


def pairs(arm):
    result = {}
    if is_rig_studio(arm):
        metadata = {pb.name: bone_meta(pb) for pb in arm.pose.bones}
        for name, meta in metadata.items():
            opposite = {'L': 'R', 'R': 'L'}.get(meta.get('side')) if meta else None
            flipped = bpy.utils.flip_name(name)
            partner = metadata.get(flipped)
            # role은 척추·손가락·IK 체인 안에서 반복된다. 이름으로 본을 특정한다.
            valid = (opposite is not None and partner is not None
                     and partner.get('side') == opposite
                     and all(meta.get(key) == partner.get(key) for key in ('type', 'part', 'role')))
            result[name] = flipped if valid else name
    else:
        for pb in arm.pose.bones:
            flipped = bpy.utils.flip_name(pb.name)
            result[pb.name] = flipped if flipped in arm.pose.bones else pb.name
    return result


def _side(arm, name):
    if is_rig_studio(arm):
        meta = bone_meta(arm.pose.bones[name])
        return meta.get('side') if meta else None
    # Blender가 지원하는 좌우 접두·접미 이름을 방향 모드에서만 판별한다.
    stem = re.sub(r'\.\d+$', '', name).lower()
    if stem.startswith('left') or stem.endswith('left'):
        return 'L'
    if stem.startswith('right') or stem.endswith('right'):
        return 'R'
    match = re.search(r'(?:^|[._ -])([lr])(?:$|[._ -])', stem)
    return match.group(1).upper() if match else None


def mirror_pose(arm, bones, mode='SWAP'):
    """레스트 축으로 보정한 LOCAL basis 목표. 부모/MCH 평가 순서와 무관하다."""
    mapping = pairs(arm)
    sources = set(bones)
    sources.update(mapping[name] for name in tuple(sources))
    return mirror_matrices(arm, {name: arm.pose.bones[name].matrix_basis for name in sources}, mode,
                           properties={name: numeric_properties(arm.pose.bones[name]) for name in sources})


def mirror_matrices(arm, matrices, mode='SWAP', *, properties=None, frame=None, source_frame=None,
                    current_mode_only=True):
    """저장한 LOCAL basis도 현재 포즈와 같은 레스트 보정식으로 반사한다."""
    if mode not in {'SWAP', 'LEFT_TO_RIGHT', 'RIGHT_TO_LEFT'}:
        raise StudioError("지원하지 않는 미러 모드입니다.")
    mapping = pairs(arm)
    sources = set(matrices)
    active, skipped = active_mode_controls(arm, sorted(sources | {mapping[n] for n in sources}), frame)
    active = set(active)
    source_active, source_skipped = active_mode_controls(arm, sorted(sources),
                                                        frame if source_frame is None else source_frame)
    skipped.extend(entry for entry in source_skipped if entry not in skipped)
    if not current_mode_only:
        active, source_active, skipped = sources | {mapping[n] for n in sources}, sources, []
    reflection = Matrix.Diagonal((-1.0, 1.0, 1.0))
    output, custom = {}, {}
    for name in sorted(sources):
        partner = mapping[name]
        if name not in source_active or partner not in active:
            continue
        if mode != 'SWAP':
            if name == partner:
                continue
            side = _side(arm, name)
            if side is None:
                raise StudioError("본 이름에서 좌우 방향을 찾을 수 없습니다: " + name)
            if side != ('L' if mode == 'LEFT_TO_RIGHT' else 'R'):
                continue
        source, target = arm.pose.bones[name], arm.pose.bones[partner]
        basis_map = (target.bone.matrix_local.to_3x3().inverted() @ reflection
                     @ source.bone.matrix_local.to_3x3()).to_4x4()
        output[partner] = basis_map @ Matrix(matrices[name]) @ basis_map.inverted()
        custom[partner] = (properties or {}).get(name, {}).copy()
    return PoseTargets(output, custom, space='LOCAL', skipped=skipped)
