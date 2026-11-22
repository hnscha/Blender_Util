# SPDX-License-Identifier: GPL-3.0-or-later
from ..core.errors import StudioError
from dataclasses import dataclass, field
from math import sqrt

import bpy
from bpy.props import EnumProperty
from mathutils import Quaternion

from ..core import fcurve
from ..core.context import (active_armature, active_mode_controls, bone_meta, control_bones,
                            frame_range, is_rig_studio, mode_skip_message)


def set_result(context, message, ok=True):
    context.window_manager.anim_studio_last = message
    context.window_manager.anim_studio_last_ok = ok
    for area in context.screen.areas if context.screen else ():
        if area.type == 'VIEW_3D':
            area.tag_redraw()


class StudioOperator:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def description(cls, _context, properties):
        for buttons in BUTTONS.values():
            for button in buttons:
                if button.operator == cls.bl_idname and all(
                        getattr(properties, key) == value for key, value in button.properties.items()):
                    return (cls.bl_description if cls.bl_description.split('\n', 1)[0] == button.label
                            else button.label + '\n' + cls.bl_description)
        return cls.bl_description

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def execute(self, context):
        try:
            message = self.run(context)
        except StudioError as error:
            message = "실행 실패: " + str(error)
            self.report({'ERROR'}, message)
            set_result(context, message, False)
            return {'CANCELLED'}
        set_result(context, message)
        return {'FINISHED'}


def _selected_targets(context):
    if context.mode == 'POSE':
        return [(pb.id_data, pb) for pb in context.selected_pose_bones or ()]
    if context.mode == 'OBJECT':
        return [(obj, obj) for obj in context.selected_editable_objects]
    raise StudioError("포즈 모드 또는 오브젝트 모드에서 실행해 주세요.")


def _tool_channels(context):
    tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    return {'builtin.move': {'location'}, 'builtin.rotate': {'rotation'},
            'builtin.scale': {'scale'}}.get(tool.idname if tool else '', fcurve.TRANSFORMS)


def _key_targets(context, targets, channels, delete=False):
    mode_skipped, active = [], {}
    for obj, owner in targets:
        if isinstance(owner, bpy.types.PoseBone) and obj not in active:
            names, skipped = active_mode_controls(obj, [pb.name for arm, pb in targets if arm == obj])
            active[obj] = set(names)
            mode_skipped.extend(skipped)
    targets = [(obj, owner) for obj, owner in targets if obj not in active or owner.name in active[obj]]
    message = mode_skip_message(mode_skipped)
    if not targets and message:
        return message
    if delete:
        with fcurve.preserve_current_pose({obj for obj, _owner in targets}):
            result = _key_targets_impl(context, targets, channels, delete=True)
    else:
        result = _key_targets_impl(context, targets, channels)
    return result + (' · ' + message if message else '')


def _key_targets_impl(context, targets, channels, delete=False):
    if not targets:
        raise StudioError("키를 처리할 본 또는 오브젝트를 선택해 주세요.")
    frame = context.scene.frame_current_final
    count, skipped = 0, []
    bags = {}
    for obj, owner in targets:
        entries = []
        for path, index, value in fcurve.transform_channels(owner, channels):
            reason = fcurve.channel_skip_reason(obj, path, index)
            if reason:
                skipped.append(f"{owner.name}: {path}[{index}] · {reason}")
            else:
                entries.append((path, index, value))
        if not entries:
            continue
        if obj not in bags:
            cb = fcurve.channelbag(obj, create=not delete)
            bags[obj] = cb, fcurve._owners(cb) if cb is not None else []
        cb, owners = bags[obj]
        for path, index, value in entries:
            if delete:
                count += fcurve.delete_current_key(obj, path, index, cb=cb, owners=owners)
            else:
                rejected = fcurve.write_current_key(obj, path, index, value, owner.name, cb=cb, owners=owners)
                skipped.extend(f"{name}: {reason}" for name, reason in rejected)
                count += not rejected
    if skipped:
        print("Anim Studio · 건너뛴 채널\n" + "\n".join(skipped))
    if count == 0:
        raise StudioError("처리할 키가 없습니다. 잠금·드라이버·기존 키를 확인해 주세요.")
    if not delete:
        context.view_layer.update()
    verb = "삭제" if delete else "기록"
    return f"{len(targets)}개 대상 · {frame:g}프레임 · {count}키 {verb}" + (f" · {len(skipped)} 제외" if skipped else '')


class ANIMSTUDIO_OT_key_all_controls(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.key_all_controls'
    bl_label = 'Anim Studio: All Controls Key'
    bl_description = '활성 리그의 모든 컨트롤에 현재 프레임 키를 넣고 잠금·드라이버 채널은 제외합니다'

    @classmethod
    def poll(cls, context):
        return active_armature(context) is not None and context.mode in {'POSE', 'OBJECT'}

    def run(self, context):
        arm = active_armature(context)
        return _key_targets(context, [(arm, pb) for pb in control_bones(arm)], fcurve.TRANSFORMS)


class ANIMSTUDIO_OT_key_tool_channels(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.key_tool_channels'
    bl_label = 'Anim Studio: Set Key (Track Mode)'
    bl_description = '현재 이동·회전·스케일 도구의 채널에만 키를 넣고 그 외 도구에서는 세 채널을 넣습니다'

    def run(self, context):
        return _key_targets(context, _selected_targets(context), _tool_channels(context))


class ANIMSTUDIO_OT_selected_key(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.selected_key'
    bl_label = 'Anim Studio: Selected Key'
    bl_description = '선택한 본에 키를 넣고 Rig Studio에서는 현재 FK/IK 모드의 컨트롤만 기록합니다'

    def run(self, context):
        if context.mode == 'POSE' and any(is_rig_studio(pb.id_data) for pb in context.selected_pose_bones or ()):
            return _key_targets(context, _selected_targets(context), fcurve.TRANSFORMS)
        if not bpy.ops.anim.keyframe_insert.poll():
            raise StudioError('키를 넣을 본 또는 오브젝트를 선택해 주세요.')
        if bpy.ops.anim.keyframe_insert() != {'FINISHED'}:
            raise StudioError('선택한 대상에 키를 기록하지 못했습니다.')
        return '선택한 대상 · 키 기록'


class ANIMSTUDIO_OT_delete_tool_channels(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.delete_tool_channels'
    bl_label = 'Anim Studio: Delete Key (Track Mode)'
    bl_description = '현재 도구에 해당하는 선택 대상의 현재 프레임 키를 삭제합니다'

    def run(self, context):
        return _key_targets(context, _selected_targets(context), _tool_channels(context), delete=True)


class ANIMSTUDIO_OT_delete_keys_outside(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.delete_keys_outside'
    bl_label = 'Anim Studio: Delete Keys Outside Range'
    bl_description = '선택 본 또는 선택 오브젝트의 미리보기 범위 밖 키를 삭제하고 미리보기가 꺼져 있으면 씬 범위를 사용합니다'

    def run(self, context):
        targets = _selected_targets(context)
        if not targets:
            raise StudioError("본 또는 오브젝트를 선택해 주세요.")
        start, end, reason = frame_range(context)
        by_object = {}
        for obj, owner in targets:
            by_object.setdefault(obj, []).append(owner)
        by_slot = {}
        for obj, owners in by_object.items():
            cb = fcurve.channelbag(obj, create=False)
            if cb is None:
                continue
            paths = None
            if context.mode == 'POSE':
                prefixes = tuple(owner.path_from_id() for owner in owners)
                paths = {curve.data_path for curve in cb.fcurves
                         if any(curve.data_path.startswith(prefix + '.') or
                                curve.data_path.startswith(prefix + '[') for prefix in prefixes)}
            key = (cb.id_data.as_pointer(), cb.slot_handle)
            if key in by_slot:
                other = by_slot[key][2]
                if any(fcurve.action_frame(obj, f) != fcurve.action_frame(other, f) for f in (start, end)):
                    raise StudioError("공유 액션의 NLA 시간이 다릅니다. 편집할 오브젝트를 하나 선택해 주세요.")
                if paths is not None:
                    by_slot[key][1].update(paths)
            else:
                by_slot[key] = (cb, paths, obj)
        plans = [fcurve.prepare_delete_outside(cb, start, end, data_paths=paths, time_owner=obj)
                 for cb, paths, obj in by_slot.values()]
        with fcurve.preserve_current_pose(by_object):
            count = sum(fcurve.apply_delete_outside(plan) for plan in plans)
        return f"{len(targets)}개 선택 · {start}–{end} ({reason}) · {count}키 삭제"


def _toggle_orientation(context):
    slot = context.scene.transform_orientation_slots[0]
    slot.type = 'LOCAL' if slot.type == 'GLOBAL' else 'GLOBAL'
    return "변환 방향 · " + slot.type


class ANIMSTUDIO_OT_toggle_orientation(StudioOperator, bpy.types.Operator):
    bl_options = {'REGISTER'}
    bl_idname = 'anim_studio.toggle_orientation'
    bl_label = 'Anim Studio: Toggle World Local'
    bl_description = '변환 방향을 월드와 로컬 사이에서 전환합니다'

    def run(self, context):
        return _toggle_orientation(context)


class ANIMSTUDIO_OT_tool_orientation(StudioOperator, bpy.types.Operator):
    bl_options = {'REGISTER'}
    bl_idname = 'anim_studio.tool_orientation'
    bl_label = 'Anim Studio: Tool World Local'
    bl_description = '지정 도구로 바꾸고 이미 같은 도구이면 월드와 로컬 방향을 전환합니다'

    tool: EnumProperty(name='Tool', description='전환할 변환 도구', items=(
        ('MOVE', 'Move', '이동 도구'), ('ROTATE', 'Rotate', '회전 도구'),
        ('SCALE', 'Scale', '스케일 도구')))

    def run(self, context):
        wanted = 'builtin.' + {'MOVE': 'move', 'ROTATE': 'rotate', 'SCALE': 'scale'}[self.tool]
        current = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
        if current is not None and current.idname == wanted:
            return _toggle_orientation(context)
        region = next(region for region in context.area.regions if region.type == 'WINDOW')
        with context.temp_override(area=context.area, region=region):
            result = bpy.ops.wm.tool_set_by_id(name=wanted)
        if result != {'FINISHED'}:
            raise StudioError("현재 모드에서 변환 도구를 선택할 수 없습니다.")
        return "변환 도구 · " + self.tool


def _armatures(context):
    return sorted((obj for obj in context.scene.objects if obj.type == 'ARMATURE'
                   and obj.name in context.view_layer.objects and obj.visible_get()
                   and not obj.hide_select), key=lambda obj: obj.name)


def _activate_armature(context, arm, pose=False):
    if arm not in _armatures(context):
        raise StudioError("현재 뷰 레이어에서 선택할 수 없는 아마추어입니다.")
    if pose and (not arm.is_editable or not arm.data.is_editable):
        raise StudioError("편집할 수 없는 아마추어는 포즈 모드로 바꿀 수 없습니다.")
    if context.object is not None and context.object.mode != 'OBJECT':
        if not bpy.ops.object.mode_set.poll():
            raise StudioError("현재 오브젝트의 모드를 전환할 수 없습니다. 표시 상태를 확인해 주세요.")
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in context.selected_objects:
        obj.select_set(False)
    arm.select_set(True)
    context.view_layer.objects.active = arm
    if pose:
        bpy.ops.object.mode_set(mode='POSE')


class ANIMSTUDIO_OT_select_com(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.select_com'
    bl_label = 'Anim Studio: Select COM'
    bl_description = 'Rig Studio 리그 전용: 포즈 모드에서 무게중심 컨트롤을 선택합니다'

    @classmethod
    def poll(cls, context):
        if not is_rig_studio(active_armature(context)):
            cls.poll_message_set("Rig Studio 리그 전용")
            return False
        return True

    def run(self, context):
        arm = active_armature(context)
        bones = [pb for pb in control_bones(arm)
                 if (meta := bone_meta(pb)) is not None and meta.get('role') == 'com']
        if not bones:
            raise StudioError("무게중심 컨트롤을 찾을 수 없습니다.")
        if any(pb.hide or pb.bone.hide_select or
               (pb.bone.collections and not any(c.is_visible_effectively for c in pb.bone.collections))
               for pb in bones):
            raise StudioError("무게중심 컨트롤이 숨겨져 있거나 선택이 잠겨 있습니다. 표시·선택 상태를 확인해 주세요.")
        _activate_armature(context, arm, pose=True)
        for pb in arm.pose.bones:
            pb.select = False
        for pb in bones:
            pb.select = True
        arm.data.bones.active = bones[0].bone
        return f"{arm.name} · 무게중심 {len(bones)}개 선택"


class ANIMSTUDIO_OT_select_next_armature(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.select_next_armature'
    bl_label = 'Anim Studio: Select Next Armature'
    bl_description = '현재 뷰 레이어에서 보이는 씬 아마추어를 이름순으로 순환 선택합니다'

    def run(self, context):
        arms = _armatures(context)
        if not arms:
            raise StudioError("선택할 수 있는 아마추어가 없습니다.")
        current = active_armature(context)
        index = (arms.index(current) + 1) % len(arms) if current in arms else 0
        arm = arms[index]
        _activate_armature(context, arm, pose=context.mode == 'POSE')
        return arm.name + " · 이름순 선택"


_armature_enum_items = []


def _armature_enum(_self, context):
    global _armature_enum_items
    _armature_enum_items = [(obj.name, obj.name, "아마추어 선택") for obj in _armatures(context)]
    return _armature_enum_items


class ANIMSTUDIO_OT_choose_armature(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.choose_armature'
    bl_label = 'Anim Studio: Choose Armature'
    bl_description = '이름 검색 팝업에서 활성 아마추어를 선택합니다'
    bl_property = 'armature'

    armature: EnumProperty(name='Armature', description='활성화할 아마추어', items=_armature_enum)

    def invoke(self, context, _event):
        if not _armatures(context):
            message = "선택할 수 있는 아마추어가 없습니다."
            self.report({'ERROR'}, message)
            set_result(context, message, False)
            return {'CANCELLED'}
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def run(self, context):
        arm = context.scene.objects.get(self.armature)
        if arm is None or arm.type != 'ARMATURE':
            raise StudioError("선택할 아마추어가 없습니다. 목록에서 다시 선택해 주세요.")
        _activate_armature(context, arm, pose=context.mode == 'POSE')
        return arm.name + " · 검색 선택"


VISIBILITY = {
    'MESH': ('mesh',), 'NON_MESH': ('armature', 'empty', 'curve', 'camera', 'light'),
    'ARMATURE': ('armature',), 'EMPTY': ('empty',), 'CURVE': ('curve',), 'CAMERA': ('camera',),
}


def visibility_on(space, kind):
    return any(getattr(space, 'show_object_viewport_' + item) for item in VISIBILITY[kind])


class ANIMSTUDIO_OT_toggle_type_visibility(StudioOperator, bpy.types.Operator):
    bl_options = {'REGISTER'}
    bl_idname = 'anim_studio.toggle_type_visibility'
    bl_label = 'Anim Studio: Toggle Type Visibility'
    bl_description = '현재 3D 뷰에서 해당 종류의 오브젝트 표시를 전환합니다'

    kind: EnumProperty(name='Type', description='표시를 전환할 종류', items=(
        ('MESH', 'Geometry', '메시'), ('NON_MESH', 'Non Geometry', '아마추어·엠프티·커브·카메라·라이트'),
        ('ARMATURE', 'Bones', '아마추어'), ('EMPTY', 'Helpers', '엠프티'),
        ('CURVE', 'Shapes', '커브'), ('CAMERA', 'Cameras', '카메라')))

    def run(self, context):
        enabled = not visibility_on(context.space_data, self.kind)
        for item in VISIBILITY[self.kind]:
            setattr(context.space_data, 'show_object_viewport_' + item, enabled)
        return f"{self.kind} · 표시 {'켬' if enabled else '끔'}"


class ANIMSTUDIO_OT_view_flip(StudioOperator, bpy.types.Operator):
    bl_options = {'REGISTER'}
    bl_idname = 'anim_studio.view_flip'
    bl_label = 'Anim Studio: View Opposite'
    bl_description = '지정 축의 첫 정면 뷰로 이동하고 이미 그 뷰이면 반대편으로 전환합니다'

    axis: EnumProperty(name='Axis', description='바라볼 축', items=(
        ('X', 'Left/Right', '왼쪽과 오른쪽'), ('Z', 'Top/Bottom', '위와 아래'),
        ('Y', 'Front/Back', '앞과 뒤')))

    def run(self, context):
        first, second, rotation = {
            'X': ('LEFT', 'RIGHT', Quaternion((0.5, 0.5, -0.5, -0.5))),
            'Z': ('TOP', 'BOTTOM', Quaternion((1, 0, 0, 0))),
            'Y': ('FRONT', 'BACK', Quaternion((sqrt(0.5), sqrt(0.5), 0, 0))),
        }[self.axis]
        view = context.space_data.region_3d
        current = view.view_rotation
        error = min(sum((a - sign * b) ** 2 for a, b in zip(current, rotation))
                    for sign in (-1, 1))
        target = second if error < 1e-8 else first
        region = next(region for region in context.area.regions if region.type == 'WINDOW')
        with context.temp_override(area=context.area, region=region):
            result = bpy.ops.view3d.view_axis(type=target, align_active=False)
        if result != {'FINISHED'}:
            raise StudioError("뷰 방향을 바꿀 수 없습니다.")
        return "뷰 방향 · " + target


def _camera(context):
    camera = context.scene.camera
    if camera is None:
        raise StudioError("씬 카메라를 지정해 주세요.")
    if camera.type != 'CAMERA':
        raise StudioError("씬 카메라에는 카메라 타입 오브젝트를 지정해 주세요.")
    return camera.data


class ANIMSTUDIO_OT_toggle_safe_areas(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.toggle_safe_areas'
    bl_label = 'Anim Studio: Toggle Safe Frames'
    bl_description = '씬 카메라의 안전 영역 표시를 전환합니다'

    def run(self, context):
        camera = _camera(context)
        camera.show_safe_areas = not camera.show_safe_areas
        return "안전 영역 · " + ('켬' if camera.show_safe_areas else '끔')


class ANIMSTUDIO_OT_toggle_thirds(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.toggle_thirds'
    bl_label = 'Anim Studio: Toggle Thirds Guide'
    bl_description = '씬 카메라의 삼분할 구도 가이드 표시를 전환합니다'

    def run(self, context):
        camera = _camera(context)
        camera.show_composition_thirds = not camera.show_composition_thirds
        return "삼분할 가이드 · " + ('켬' if camera.show_composition_thirds else '끔')


class ANIMSTUDIO_OT_easy_resolution(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.easy_resolution'
    bl_label = 'Anim Studio: Easy Set 640x360'
    bl_description = '렌더 해상도를 640×360, 배율을 100%로 설정합니다'

    def run(self, context):
        render = context.scene.render
        render.resolution_x, render.resolution_y, render.resolution_percentage = 640, 360, 100
        return "렌더 해상도 · 640×360 · 100%"


class ANIMSTUDIO_OT_focus_panel(StudioOperator, bpy.types.Operator):
    bl_options = {'REGISTER'}
    bl_idname = 'anim_studio.focus_panel'
    bl_label = 'Anim Studio: Focus Anim Studio'
    bl_description = '3D 뷰 사이드바를 열고 Anim Studio 탭을 선택합니다'

    def run(self, context):
        area = context.area
        area.spaces.active.show_region_ui = True
        region = next(region for region in area.regions if region.type == 'UI')
        if region.is_property_readonly('active_panel_category'):
            return "사이드바를 열었습니다. 탭 선택은 한 번 더 누르세요."
        region.active_panel_category = 'Anim Studio'
        if region.active_panel_category != 'Anim Studio':
            return "사이드바를 열었습니다. 탭 선택은 한 번 더 누르세요."
        return "Anim Studio · 사이드바 선택"


class ANIMSTUDIO_OT_open_keymap(StudioOperator, bpy.types.Operator):
    bl_options = {'REGISTER'}
    bl_idname = 'anim_studio.open_keymap'
    bl_label = 'Anim Studio: Keymap'
    bl_description = '환경설정 키맵을 열고 anim_studio로 검색합니다'

    def run(self, context):
        context.preferences.active_section = 'KEYMAP'
        bpy.ops.screen.userpref_show('INVOKE_DEFAULT')
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'PREFERENCES':
                    area.spaces.active.filter_type = 'NAME'
                    area.spaces.active.filter_text = 'anim_studio'
                    area.tag_redraw()
        return "키맵 · anim_studio 검색"


@dataclass(frozen=True)
class Button:
    label: str
    operator: str
    icon: str
    properties: dict = field(default_factory=dict)
    keymaps: tuple = ('3D View',)
    alert: bool = False
    primary: bool = False
    short: str = ""


BUTTONS = {
    'KEY': (
        Button('All Controls Key', 'anim_studio.key_all_controls', 'key_all', keymaps=('Pose', 'Object Mode'), primary=True, short='All'),
        Button('Selected Key', 'anim_studio.selected_key', 'key_selected', keymaps=('Pose', 'Object Mode'), short='Sel'),
        Button('Set Key (Track Mode)', 'anim_studio.key_tool_channels', 'key_track', keymaps=('Pose', 'Object Mode'), short='Track'),
        Button('Delete Key (Track Mode)', 'anim_studio.delete_tool_channels', 'del_track', keymaps=('Pose', 'Object Mode'), short='Del'),
        Button('Delete Sel Keys', 'anim.keyframe_delete_v3d', 'del_selected', keymaps=('Pose', 'Object Mode'), short='Sel'),
        Button('Delete Keys Outside Range', 'anim_studio.delete_keys_outside', 'del_outside', keymaps=('Pose', 'Object Mode'), alert=True, short='Out'),
        Button('Delete All Timeline Keys', 'anim.keyframe_clear_v3d', 'del_all', keymaps=('Pose', 'Object Mode'), alert=True, short='Clear'),
    ),
    'TRANSFORM': (
        Button('Toggle World Local', 'anim_studio.toggle_orientation', 'toggle_world_local', short='Space'),
        Button('Move World Local', 'anim_studio.tool_orientation', 'move_world_local', {'tool': 'MOVE'}, short='Move'),
        Button('Rotate World Local', 'anim_studio.tool_orientation', 'rotate_world_local', {'tool': 'ROTATE'}, short='Rot'),
        Button('Scale World Local', 'anim_studio.tool_orientation', 'scale_world_local', {'tool': 'SCALE'}, short='Scale'),
        Button('Free Rotation', 'transform.trackball', 'free_rotation', short='Free'),
    ),
    'SELECT': (
        Button('Select Opposite', 'pose.select_mirror', 'select_opposite', keymaps=('Pose',), short='Flip'),
        Button('Select COM', 'anim_studio.select_com', 'select_com', keymaps=('Pose', 'Object Mode'), short='COM'),
        Button('Select Next Armature', 'anim_studio.select_next_armature', 'next_armature', short='Next'),
        Button('Choose Armature', 'anim_studio.choose_armature', 'choose_armature', short='Find'),
        Button('Easy Parent', 'object.parent_set', 'easy_parent', keymaps=('Object Mode',), short='Link'),
    ),
    'DISPLAY': (
        Button('Toggle Geometry', 'anim_studio.toggle_type_visibility', 'vis_geometry', {'kind': 'MESH'}, short='Mesh'),
        Button('Toggle Non Geometry', 'anim_studio.toggle_type_visibility', 'vis_non_geometry', {'kind': 'NON_MESH'}, short='Other'),
        Button('Toggle Bones', 'anim_studio.toggle_type_visibility', 'vis_bones', {'kind': 'ARMATURE'}, short='Bones'),
        Button('Toggle Helpers', 'anim_studio.toggle_type_visibility', 'vis_helpers', {'kind': 'EMPTY'}, short='Help'),
        Button('Toggle Shapes', 'anim_studio.toggle_type_visibility', 'vis_shapes', {'kind': 'CURVE'}, short='Curve'),
        Button('Toggle Cameras', 'anim_studio.toggle_type_visibility', 'vis_cameras', {'kind': 'CAMERA'}, short='Cam'),
    ),
    'VIEW': (
        Button('View Left/Right', 'anim_studio.view_flip', 'view_left_right', {'axis': 'X'}, short='L/R'),
        Button('View Top/Bottom', 'anim_studio.view_flip', 'view_top_bottom', {'axis': 'Z'}, short='T/B'),
        Button('View Front/Back', 'anim_studio.view_flip', 'view_front_back', {'axis': 'Y'}, short='F/B'),
        Button('Toggle Safe Frames', 'anim_studio.toggle_safe_areas', 'safe_frames', short='Safe'),
        Button('Toggle Thirds Guide', 'anim_studio.toggle_thirds', 'thirds_guide', short='Third'),
        Button('Easy Set 640x360', 'anim_studio.easy_resolution', 'easy_resolution', short='640'),
        Button('Toggle Pose Library', 'wm.context_toggle', 'pose_library', {'data_path': 'space_data.show_region_asset_shelf'}, short='Pose'),
        Button('Focus Anim Studio', 'anim_studio.focus_panel', 'focus_panel', short='Panel'),
    ),
    'POSE_TOOLS': (
        Button('Copy', 'anim_studio.pose_copy', 'pose_library', keymaps=('Pose',), short='Copy'),
        Button('Paste', 'anim_studio.pose_paste', 'pose_library', keymaps=('Pose',), short='Paste'),
        Button('Paste Opposite', 'anim_studio.pose_paste_opposite', 'select_opposite', keymaps=('Pose',), short='Opp'),
        Button('Paste Range', 'anim_studio.pose_paste_range', 'pose_library', keymaps=('Pose',), short='Range'),
        Button('Mirror Pose', 'anim_studio.mirror_pose', 'select_opposite', keymaps=('Pose',), short='Mir'),
        Button('Mirror Range', 'anim_studio.mirror_range', 'select_opposite', keymaps=('Pose',), short='Range'),
        Button('First Half → Second Half', 'anim_studio.mirror_loop', 'select_opposite', {'half': 'FIRST'}, keymaps=('Pose',), short='1→2'),
        Button('Second Half → First Half', 'anim_studio.mirror_loop', 'select_opposite', {'half': 'SECOND'}, keymaps=('Pose',), short='2→1'),
        Button('Mirror Single Frame', 'anim_studio.mirror_loop_single', 'select_opposite', keymaps=('Pose',), short='Frame'),
        Button('Tween…', 'anim_studio.tween', 'free_rotation', keymaps=('Pose',), short='Tween'),
        Button('Tween 50', 'anim_studio.tween_set', 'free_rotation', {'factor': 0.5}, keymaps=('Pose',), short='50'),
        Button('Key Match Prev', 'anim_studio.key_match', 'key_track', {'direction': 'PREV'}, keymaps=('Pose',), short='Prev'),
        Button('Key Match Next', 'anim_studio.key_match', 'key_track', {'direction': 'NEXT'}, keymaps=('Pose',), short='Next'),
        Button('Create Temp Pivot', 'anim_studio.temp_pivot_create', 'vis_helpers', keymaps=('Pose',), short='Pivot'),
        Button('Apply Temp Pivot', 'anim_studio.temp_pivot_apply', 'vis_helpers', short='Apply'),
        Button('Bake Temp Pivot', 'anim_studio.temp_pivot_bake', 'vis_helpers', short='Bake'),
        Button('Delete Temp Pivot', 'anim_studio.temp_pivot_delete', 'vis_helpers', short='Del'),
        Button('Lock Range', 'anim_studio.world_lock_range', 'toggle_world_local', keymaps=('Pose',), short='Lock'),
        Button('Set from Selection', 'anim_studio.align_set', 'move_world_local', short='Set'),
        Button('Align Now', 'anim_studio.align_now', 'move_world_local', short='Now'),
        Button('Align Range', 'anim_studio.align_range', 'move_world_local', short='Range'),
        Button('Delete Pose Record', 'anim_studio.pose_history_delete', 'pose_library', short='Del'),
    ),
}

_CLASSES = (
    ANIMSTUDIO_OT_key_all_controls, ANIMSTUDIO_OT_key_tool_channels, ANIMSTUDIO_OT_selected_key,
    ANIMSTUDIO_OT_delete_tool_channels, ANIMSTUDIO_OT_delete_keys_outside,
    ANIMSTUDIO_OT_toggle_orientation, ANIMSTUDIO_OT_tool_orientation,
    ANIMSTUDIO_OT_select_com, ANIMSTUDIO_OT_select_next_armature,
    ANIMSTUDIO_OT_choose_armature, ANIMSTUDIO_OT_toggle_type_visibility,
    ANIMSTUDIO_OT_view_flip, ANIMSTUDIO_OT_toggle_safe_areas, ANIMSTUDIO_OT_toggle_thirds,
    ANIMSTUDIO_OT_easy_resolution, ANIMSTUDIO_OT_focus_panel, ANIMSTUDIO_OT_open_keymap,
)
_addon_keymaps = []


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    config = bpy.context.window_manager.keyconfigs.addon
    if config is None:  # Blender 백그라운드 실행에는 키맵 자체가 없다.
        return
    for buttons in BUTTONS.values():
        for button in buttons:
            if not button.operator.startswith('anim_studio.'):
                continue
            for name in button.keymaps:
                km = config.keymaps.new(name=name, space_type='VIEW_3D' if name == '3D View' else 'EMPTY')
                item = km.keymap_items.new(button.operator, type='NONE', value='PRESS')
                for prop, value in button.properties.items():
                    setattr(item.properties, prop, value)
                _addon_keymaps.append((km, item))


def unregister():
    for km, item in reversed(_addon_keymaps):
        km.keymap_items.remove(item)
    _addon_keymaps.clear()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
