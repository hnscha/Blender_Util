# SPDX-License-Identifier: GPL-3.0-or-later
"""현재 파일의 Blender 포즈 애셋을 LOCAL 목표로 읽는다. frame_set은 사용하지 않는다."""
import json
import re
import tempfile
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

from . import fcurve, mirror, pose_tools as pose, snapshot
from .context import active_mode_controls, busy
from .errors import StudioError

_BONE = re.compile(r'^pose\.bones\[("(?:[^"\\]|\\.)*")\](.*)$')
_TRANSFORMS = {'location', 'scale', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle'}
BBONES = ('bbone_curveinx', 'bbone_curveoutx', 'bbone_curveinz', 'bbone_curveoutz',
          'bbone_rollin', 'bbone_rollout', 'bbone_scalein', 'bbone_scaleout', 'bbone_easein', 'bbone_easeout')


def bags(action):
    for layer in action.layers:
        for strip in layer.strips:
            if strip.type == 'KEYFRAME':
                yield from strip.channelbags


def is_pose_asset(action):
    return (action.library is None and action.asset_data is not None
            and abs(action.frame_range[1] - action.frame_range[0]) < 1e-5
            and any(_BONE.match(fc.data_path) for cb in bags(action) for fc in cb.fcurves))


def assets():
    return sorted((a for a in bpy.data.actions if is_pose_asset(a)), key=lambda a: a.name.casefold())


def catalogs():
    result = {'': ('Unassigned', '')}
    if bpy.data.filepath:
        path = Path(bpy.data.filepath).parent / 'blender_assets.cats.txt'
        if path.is_file():
            for line in path.read_text(encoding='utf-8').splitlines():
                fields = line.split(':', 2)
                if len(fields) == 3 and not line.startswith('#'):
                    result[fields[0]] = (fields[1], fields[1])
    for action in assets():
        meta = action.asset_data
        if meta.catalog_id != '00000000-0000-0000-0000-000000000000':
            result.setdefault(meta.catalog_id, (meta.catalog_simple_name or meta.catalog_id, ''))
    return result


def save(context, name, catalog_id='', catalog_path='', thumbnail=True):
    arm, names = pose.selected(context)
    if not name.strip():
        raise StudioError('포즈 이름을 입력하세요.')
    before = {a.as_pointer() for a in bpy.data.actions}
    # 5.2.2: 3D 뷰 area를 넘기면 내부 ensure_asset_ui_visible이 셸프 region이 없을 때
    # 널 참조로 강제 종료된다(백그라운드 재현). area 없이 만들고 셸프는 패널 버튼이 연다.
    with context.temp_override(area=None, region=None):
        if not bpy.ops.poselib.create_pose_asset.poll():
            raise StudioError('이 컨텍스트에서는 포즈 애셋을 만들 수 없습니다.')
        result = bpy.ops.poselib.create_pose_asset('EXEC_DEFAULT', False, pose_name=name.strip(),
                                                  asset_library_reference='LOCAL', catalog_path=catalog_path)
    created = [a for a in bpy.data.actions if a.as_pointer() not in before and a.asset_data]
    if result != {'FINISHED'} or len(created) != 1:
        raise StudioError('포즈 애셋을 만들지 못했습니다.')
    action = created[0]
    try:
        if catalog_id:
            action.asset_data.catalog_id = catalog_id
        action['anim_studio_rotation_modes'] = json.dumps({n: arm.pose.bones[n].rotation_mode for n in names})
        if thumbnail:
            preview(context, action)
    except BaseException:
        bpy.data.actions.remove(action)
        raise
    return action


def preview(context, action):
    if bpy.app.background or context.area is None or context.area.type != 'VIEW_3D':
        raise StudioError('현재 뷰 썸네일은 3D 뷰에서 저장하세요.')
    # OS 임시 파일도 Blender의 세션 tempdir 안에만 만들고 반드시 지운다.
    try:
        with tempfile.TemporaryDirectory(prefix='anim_studio_pose_', dir=bpy.app.tempdir) as directory:
            path = str(Path(directory) / 'view.png')
            if bpy.ops.screen.screenshot_area(filepath=path) != {'FINISHED'}:
                raise StudioError('현재 뷰를 캡처하지 못했습니다.')
            path = _crop_view(context, path, str(Path(directory) / 'thumb.png'))
            with context.temp_override(id=action):
                if bpy.ops.ed.lib_id_load_custom_preview(filepath=path) != {'FINISHED'}:
                    raise StudioError('포즈 썸네일을 지정하지 못했습니다.')
    except OSError as error:
        raise StudioError('썸네일 임시 파일을 처리할 수 없습니다: ' + str(error)) from error


def _crop_view(context, source, target):
    """area 캡처에서 헤더·겹친 툴바·사이드바를 빼고 뷰 가운데 정사각형만 남긴다."""
    from array import array
    area = context.area
    window = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if window is None:
        return source
    left, right = 0, 0
    if context.preferences.system.use_region_overlap:
        for region in area.regions:
            if region.type == 'TOOLS' and region.width > 1:
                left = region.width
            elif region.type == 'UI' and region.width > 1:
                right = region.width
    image = bpy.data.images.load(source, check_existing=False)
    crop = None
    try:
        width, height = image.size
        ratio = width / max(1, area.width)
        x0 = (window.x - area.x + left) * ratio
        y0 = (window.y - area.y) * ratio
        w = (window.width - left - right) * ratio
        h = window.height * ratio
        side = int(min(w, h) * .92)
        if side < 16:
            return source
        cx, cy = int(x0 + (w - side) / 2), int(y0 + (h - side) / 2)
        cx, cy = max(0, min(cx, width - side)), max(0, min(cy, height - side))
        pixels = array('f', bytes(4 * width * height * 4))
        image.pixels.foreach_get(pixels)
        out = array('f')
        for row in range(cy, cy + side):
            start = (row * width + cx) * 4
            out.extend(pixels[start:start + side * 4])
        crop = bpy.data.images.new('anim_studio_thumb', side, side, alpha=True)
        crop.pixels.foreach_set(out)
        crop.filepath_raw = target
        crop.file_format = 'PNG'
        crop.save()
        return target
    finally:
        bpy.data.images.remove(image)
        if crop is not None:
            bpy.data.images.remove(crop)


def read(arm, action):
    if action is None or not is_pose_asset(action):
        raise StudioError('현재 파일의 포즈 애셋을 선택하세요.')
    candidates = [cb for cb in bags(action) if any(_BONE.match(fc.data_path) for fc in cb.fcurves)]
    # 단일 포즈라도 복수 slot/layer 합성은 모호하므로 조용히 잘못 적용하지 않는다.
    if len(candidates) != 1:
        raise StudioError('포즈 애셋에는 하나의 본 채널 묶음이 필요합니다.')
    records = {}
    for fc in candidates[0].fcurves:
        match = _BONE.match(fc.data_path)
        if not match:
            continue
        name, suffix = json.loads(match[1]), match[2]
        if name in arm.pose.bones:
            records.setdefault(name, {})[(suffix, fc.array_index)] = fc.evaluate(action.frame_range[0])
    matrices, properties, extras = {}, {}, {}
    modes = json.loads(action.get('anim_studio_rotation_modes', '{}'))
    for name, channels in records.items():
        pb = arm.pose.bones[name]
        vectors = {prop: list(getattr(pb, prop)) for prop in _TRANSFORMS}
        props, extra, rotations = {}, {}, set()
        for (suffix, index), value in channels.items():
            prop = suffix[1:]
            if suffix.startswith('.') and prop in _TRANSFORMS:
                if 0 <= index < len(vectors[prop]):
                    vectors[prop][index] = value
                    if prop.startswith('rotation_'):
                        rotations.add(prop)
            elif suffix.startswith('.') and prop in BBONES:
                extra[(prop, index)] = value
            elif suffix.startswith('["') and suffix.endswith('"]'):
                key = json.loads(suffix[1:-1])
                if isinstance(pb.get(key), (int, float, bool)):
                    old = pb[key]
                    props[key] = bool(value >= .5) if isinstance(old, bool) else round(value) if isinstance(old, int) else value
        if len(rotations) > 1:
            raise StudioError('포즈 애셋에 회전 표현이 중복되어 있습니다: ' + name)
        rotation = next(iter(rotations), {'QUATERNION': 'rotation_quaternion',
                                         'AXIS_ANGLE': 'rotation_axis_angle'}.get(pb.rotation_mode, 'rotation_euler'))
        if rotation == 'rotation_quaternion':
            quat = Quaternion(vectors[rotation]).normalized()
        elif rotation == 'rotation_axis_angle':
            angle, *axis = vectors[rotation]
            quat = Quaternion(axis, angle)
        else:
            order = modes.get(name, pb.rotation_mode)
            quat = Euler(vectors[rotation], order if order in {'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'} else 'XYZ').to_quaternion()
        matrices[name] = Matrix.LocRotScale(Vector(vectors['location']), quat, Vector(vectors['scale']))
        properties[name], extras[name] = props, extra
    return pose.protect_modes(arm, snapshot.PoseTargets(matrices, properties, space='LOCAL')), extras


def target(arm, action, *, selected_only=False, current_mode_only=True, flipped=False):
    result, extras = read(arm, action)
    if flipped:
        result = pose.mirror_saved(arm, result, current_mode_only=current_mode_only)
        mapping = mirror.pairs(arm)
        extras = {mapping[n]: {(p, i): -v if p in {'bbone_curveinx', 'bbone_curveoutx', 'bbone_rollin', 'bbone_rollout'} else v
                              for (p, i), v in channels.items()} for n, channels in extras.items()}
    names = [n for n in result if not selected_only or arm.pose.bones[n].select]
    if current_mode_only:
        names = active_mode_controls(arm, names)[0]
    if not names:
        raise StudioError('옵션에 맞는 적용 대상 본이 없습니다.')
    return pose.subset(result, names), {n: extras.get(n, {}) for n in names}


def extra_value(pb, prop, index):
    value = getattr(pb, prop)
    return value[index] if prop in {'bbone_scalein', 'bbone_scaleout'} else value


def set_extra(pb, prop, index, value):
    if prop in {'bbone_scalein', 'bbone_scaleout'}:
        getattr(pb, prop)[index] = value
    else:
        setattr(pb, prop, value)


def apply(arm, result, extras, *, key=False, current_mode_only=True):
    with busy():
        message = pose.apply_current(arm, result, key=key, current_mode_only=current_mode_only)
        with fcurve.preserve_current_pose([arm]):
            for name, channels in extras.items():
                pb = arm.pose.bones[name]
                for (prop, index), value in channels.items():
                    if fcurve.channel_skip_reason(arm, pb.path_from_id(prop), max(index, 0)):
                        continue
                    set_extra(pb, prop, index, value)
                    if key:
                        pb.keyframe_insert(prop, index=index if prop in {'bbone_scalein', 'bbone_scaleout'} else -1,
                                           frame=bpy.context.scene.frame_current_final, group=name)
            # 명시적인 Key는 기존 커브가 없는 애셋 커스텀 속성도 기록한다.
            if key:
                cb = fcurve.channelbag(arm)
                for name, props in result.custom_properties.items():
                    pb = arm.pose.bones[name]
                    for prop, value in props.items():
                        fcurve.write_current_key(arm, pose.property_path(pb, prop), 0, value, name,
                                                 cb=cb, custom_property=(pb, prop))
        arm.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
    return message


class BlendSession:
    """미리보기는 고정 시작점에서 계산하며 키는 확정 때 한 번만 기록한다."""
    def __init__(self, arm, result, extras, current_mode_only=True):
        self.arm_name = arm.name
        self.a, self.b = pose.current(arm, result), result
        self.extras = extras
        self.mode_only = current_mode_only
        self.original = {n: {p: tuple(getattr(arm.pose.bones[n], p)) for p in _TRANSFORMS} for n in result}
        self.properties = {n: snapshot.numeric_properties(arm.pose.bones[n]) for n in result}
        self.extra_start = {n: {(p, i): extra_value(arm.pose.bones[n], p, i) for p, i in channels}
                            for n, channels in extras.items()}

    def apply(self, factor, key=False):
        arm = bpy.data.objects.get(self.arm_name)
        if arm is None or arm.type != 'ARMATURE':
            raise StudioError('포즈 적용 대상 아마추어가 없습니다.')
        if any(n not in arm.pose.bones for n in self.b):
            raise StudioError('포즈 적용 대상 본이 변경되었습니다.')
        extras = {n: {k: self.extra_start[n][k] + (v - self.extra_start[n][k]) * factor
                      for k, v in channels.items()} for n, channels in self.extras.items()}
        return apply(arm, pose.blend(self.a, self.b, factor), extras, key=key, current_mode_only=self.mode_only)

    def restore(self):
        arm = bpy.data.objects.get(self.arm_name)
        if arm is None or arm.type != 'ARMATURE':
            return
        with busy():
            for name, channels in self.original.items():
                pb = arm.pose.bones.get(name)
                if pb is None:
                    continue
                for prop, values in channels.items():
                    setattr(pb, prop, values)
                for prop, value in self.properties[name].items():
                    pb[prop] = value
                for (prop, index), value in self.extra_start.get(name, {}).items():
                    set_extra(pb, prop, index, value)
            arm.update_tag(refresh={'OBJECT'})
            bpy.context.view_layer.update()
