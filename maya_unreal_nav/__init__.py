# SPDX-License-Identifier: GPL-3.0-or-later
"""
Maya/Unreal 식 뷰포트 조작 애드온.

  Q / W / E / R      선택(박스) / 이동 / 회전 / 스케일 툴
  1 / 2 (숫자 윗줄)   좌표계 World(Global) / Local  (넘패드는 건드리지 않음)
  Alt + 왼쪽 드래그   뷰 회전 (Orbit)
  Alt + 가운데 드래그 뷰 이동 (Pan)
  Alt + 오른쪽 드래그 뷰 줌 (Dolly)

Object / Edit / Pose 모드, Layout / Modeling / Animating 등 모든 워크스페이스에서 같다.
같은 키를 쓰는 Blender 기본 단축키는 활성 키맵에서 꺼 두고, 애드온을 끄면 기본값으로 되돌린다.
이동/회전/스케일 중에는 변환 중심에 X/Y/Z 축 화살표를 그린다 (현재 좌표계 기준).
"""

import math

import bmesh
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector
from bpy_extras import view3d_utils

# QWER 툴 전환을 넣을 모드별 키맵. Edit 모드는 편집 대상별 키맵(Mesh, Curve, Armature ...)이다.
_TOOL_KEYMAPS = (
    "Object Mode",
    "Mesh",
    "Curve",
    "Armature",
    "Pose",
    "Lattice",
    "Metaball",
)

_TOOL_KEYS = (
    ("Q", "builtin.select_box"),
    ("W", "builtin.move"),
    ("E", "builtin.rotate"),
    ("R", "builtin.scale"),
)

_NAV_KEYS = (
    ("view3d.rotate", "LEFTMOUSE"),
    ("view3d.move", "MIDDLEMOUSE"),
    ("view3d.zoom", "RIGHTMOUSE"),
)

_ORIENT_PATH = "scene.transform_orientation_slots[0].type"
_ORIENT_KEYS = (
    ("ONE", 'GLOBAL'),
    ("TWO", 'LOCAL'),
)

# 3D 뷰포트에서 이 애드온 키보다 먼저/같이 처리될 수 있는 키맵. 여기서 같은 키를 쓰는 기본 항목을 끈다.
_CONFLICT_KEYMAPS = (
    "Window",
    "Screen",
    "Frames",
    "Object Non-modal",
    "3D View Generic",
    "3D View",
) + _TOOL_KEYMAPS

_BARE_KEYS = {key for key, _ in _TOOL_KEYS} | {key for key, _ in _ORIENT_KEYS}
_ALT_MOUSE = {mouse for _, mouse in _NAV_KEYS}

# (keymap, keymap_item) 목록. unregister 에서 그대로 제거한다.
_addon_keymaps = []
_draw_handle = None


# -----------------------------------------------------------------------------
# 환경 설정

class MayaUnrealNavPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    show_axis_overlay: bpy.props.BoolProperty(
        name="변환 중 축 표시",
        description="이동/회전/스케일 중 변환 중심에 X/Y/Z 축 화살표를 그린다",
        default=True,
    )
    axis_overlay_size: bpy.props.IntProperty(
        name="축 길이 (px)",
        default=80,
        min=20,
        max=400,
    )

    def draw(self, context):
        layout = self.layout
        inputs = context.preferences.inputs

        col = layout.column(align=True)
        col.label(text="Q / W / E / R : 선택 / 이동 / 회전 / 스케일")
        col.label(text="1 / 2 : 좌표계 World / Local")
        col.label(text="Alt + 왼쪽 / 가운데 / 오른쪽 드래그 : 회전 / 이동 / 줌")

        if inputs.use_mouse_emulate_3_button:
            box = layout.box()
            box.alert = True
            box.label(text="Emulate 3 Button Mouse 가 켜져 있어 Alt+왼쪽 드래그가 겹칩니다.", icon='ERROR')
            box.prop(inputs, "use_mouse_emulate_3_button")

        box = layout.box()
        box.prop(self, "show_axis_overlay")
        row = box.row()
        row.active = self.show_axis_overlay
        row.prop(self, "axis_overlay_size")

        box = layout.box()
        box.label(text="Blender 내비게이션 설정 (선택)")
        box.prop(inputs, "use_zoom_to_mouse")
        box.prop(inputs, "use_rotate_around_active")

        disabled = _conflict_items(active_only=False)
        if disabled:
            box = layout.box()
            box.label(text="겹쳐서 꺼 둔 기본 단축키 ({:d})".format(len(disabled)))
            col = box.column(align=True)
            for km, kmi in disabled:
                col.label(text="{:s} : {:s}  ({:s})".format(km.name, _key_text(kmi), kmi.name or kmi.idname))


def _prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


# -----------------------------------------------------------------------------
# 키맵

def _register_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        # 백그라운드 모드에는 애드온 키컨피그가 없다.
        return

    km = kc.keymaps.new(name="3D View", space_type='VIEW_3D')
    for idname, mouse in _NAV_KEYS:
        kmi = km.keymap_items.new(idname, mouse, 'PRESS', alt=True)
        _addon_keymaps.append((km, kmi))
    for key, orient in _ORIENT_KEYS:
        kmi = km.keymap_items.new("wm.context_set_enum", key, 'PRESS')
        kmi.properties.data_path = _ORIENT_PATH
        kmi.properties.value = orient
        _addon_keymaps.append((km, kmi))

    for km_name in _TOOL_KEYMAPS:
        km = kc.keymaps.new(name=km_name, space_type='EMPTY')
        for key, tool in _TOOL_KEYS:
            kmi = km.keymap_items.new("wm.tool_set_by_id", key, 'PRESS')
            kmi.properties.name = tool
            _addon_keymaps.append((km, kmi))


def _unregister_keymaps():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _addon_keymaps.clear()


def _key_text(kmi):
    return ("Alt+" if kmi.alt else "") + kmi.type + "/" + kmi.value


def _is_own(km_name, kmi):
    """활성 키맵에 합쳐진 이 애드온의 항목인지."""
    if km_name == "3D View":
        if kmi.alt and any(kmi.idname == i and kmi.type == m for i, m in _NAV_KEYS):
            return True
        if kmi.idname == "wm.context_set_enum":
            props = kmi.properties
            return any(
                kmi.type == k and props.data_path == _ORIENT_PATH and props.value == v
                for k, v in _ORIENT_KEYS
            )
    if km_name in _TOOL_KEYMAPS and kmi.idname == "wm.tool_set_by_id":
        return any(kmi.type == k and kmi.properties.name == t for k, t in _TOOL_KEYS)
    return False


def _is_conflict(kmi):
    if kmi.key_modifier != 'NONE' or kmi.shift or kmi.ctrl or kmi.oskey or kmi.hyper:
        return False
    if kmi.type in _BARE_KEYS:
        return not kmi.alt
    if kmi.type in _ALT_MOUSE:
        return kmi.alt
    return False


def _conflict_items(active_only=True):
    kc = bpy.context.window_manager.keyconfigs.user
    if kc is None:
        return []
    found = []
    for km_name in _CONFLICT_KEYMAPS:
        km = kc.keymaps.get(km_name)
        if km is None:
            continue
        for kmi in km.keymap_items:
            if not _is_conflict(kmi) or _is_own(km_name, kmi):
                continue
            if active_only and not kmi.active:
                continue
            if not active_only and kmi.active:
                continue
            found.append((km, kmi))
    return found


def _disable_conflicts():
    for _km, kmi in _conflict_items(active_only=True):
        kmi.active = False
    return None  # 타이머 1회 실행.


def _restore_conflicts():
    for km, kmi in _conflict_items(active_only=False):
        try:
            km.restore_item_to_default(kmi)
        except (ReferenceError, RuntimeError):
            kmi.active = True


# -----------------------------------------------------------------------------
# 변환 중 축 표시

_AXIS_COLORS = (
    (1.0, 0.2, 0.32, 1.0),
    (0.54, 0.86, 0.0, 1.0),
    (0.16, 0.56, 1.0, 1.0),
)


def _transform_running(context):
    win = context.window
    if win is None:
        return False
    for op in win.modal_operators:
        idname = op.bl_idname
        if idname.startswith("TRANSFORM_OT_") or idname.startswith("transform."):
            return True
    return False


def _selection_center(context):
    obj = context.active_object
    mode = context.mode

    if mode == 'EDIT_MESH' and obj is not None and obj.type == 'MESH':
        bm = bmesh.from_edit_mesh(obj.data)
        pts = [v.co for v in bm.verts if v.select]
        if pts:
            return obj.matrix_world @ (sum(pts, Vector()) / len(pts))
    elif mode == 'EDIT_ARMATURE' and obj is not None:
        pts = []
        for b in obj.data.edit_bones:
            if b.select_head:
                pts.append(b.head)
            if b.select_tail:
                pts.append(b.tail)
        if pts:
            return obj.matrix_world @ (sum(pts, Vector()) / len(pts))
    elif mode == 'POSE' and obj is not None:
        pbs = context.selected_pose_bones or []
        if pbs:
            pts = [pb.id_data.matrix_world @ pb.head for pb in pbs]
            return sum(pts, Vector()) / len(pts)
    elif mode == 'OBJECT':
        objs = context.selected_objects
        if objs:
            return sum((o.matrix_world.translation for o in objs), Vector()) / len(objs)

    if obj is not None:
        return obj.matrix_world.translation.copy()
    return None


def _orientation_matrix(context):
    slot = context.scene.transform_orientation_slots[0]
    kind = slot.type
    obj = context.active_object

    if kind == 'GLOBAL':
        return Matrix.Identity(3)
    if kind == 'VIEW':
        return context.region_data.view_rotation.to_matrix()
    if kind == 'CURSOR':
        return context.scene.cursor.matrix.to_3x3().normalized()
    if slot.custom_orientation is not None:
        return slot.custom_orientation.matrix.copy()

    # LOCAL / NORMAL / GIMBAL / PARENT: 활성 대상의 축을 쓴다.
    if obj is None:
        return Matrix.Identity(3)
    mat = obj.matrix_world
    if context.mode == 'POSE' and context.active_pose_bone is not None:
        pb = context.active_pose_bone
        if kind == 'PARENT' and pb.parent is not None:
            pb = pb.parent
        mat = obj.matrix_world @ pb.matrix
    elif kind == 'PARENT' and obj.parent is not None:
        mat = obj.parent.matrix_world
    return mat.to_3x3().normalized()


def _draw_axis_overlay():
    context = bpy.context
    prefs = _prefs()
    if prefs is None or not prefs.show_axis_overlay:
        return
    if not _transform_running(context):
        return
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return

    center = _selection_center(context)
    if center is None:
        return
    p2d = view3d_utils.location_3d_to_region_2d(region, rv3d, center)
    if p2d is None:
        return

    # 화면에서 일정한 픽셀 길이가 되도록 월드 길이를 구한다.
    right = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
    q2d = view3d_utils.location_3d_to_region_2d(region, rv3d, center + right)
    if q2d is None or (q2d - p2d).length < 1e-6:
        return
    length = prefs.axis_overlay_size / (q2d - p2d).length

    rot = _orientation_matrix(context)
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    tri_shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('ALPHA')

    for i in range(3):
        axis = rot.col[i].normalized()
        tip = center + axis * length
        shader.uniform_float("viewportSize", (region.width, region.height))
        shader.uniform_float("lineWidth", 2.5)
        shader.uniform_float("color", _AXIS_COLORS[i])
        batch_for_shader(shader, 'LINES', {"pos": [center, tip]}).draw(shader)

        # 화살촉: 축에 수직인 두 벡터로 원뿔을 만든다.
        side = axis.orthogonal().normalized()
        up = axis.cross(side).normalized()
        head_len = length * 0.18
        head_rad = length * 0.06
        base = tip - axis * head_len
        ring = []
        for s in range(12):
            a = s / 12.0 * 2.0 * math.pi
            ring.append(base + (side * math.cos(a) + up * math.sin(a)) * head_rad)
        tris = []
        for s in range(12):
            tris += [tip, ring[s], ring[(s + 1) % 12]]
        tri_shader.uniform_float("color", _AXIS_COLORS[i])
        batch_for_shader(tri_shader, 'TRIS', {"pos": tris}).draw(tri_shader)

    gpu.state.blend_set('NONE')


# -----------------------------------------------------------------------------
# 등록

def register():
    global _draw_handle
    bpy.utils.register_class(MayaUnrealNavPreferences)
    _register_keymaps()
    _disable_conflicts()
    # 시작 직후에는 활성 키맵이 아직 다 만들어지지 않았을 수 있어 한 번 더 실행한다.
    bpy.app.timers.register(_disable_conflicts, first_interval=1.0)
    if not bpy.app.background:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_axis_overlay, (), 'WINDOW', 'POST_VIEW',
        )


def unregister():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if bpy.app.timers.is_registered(_disable_conflicts):
        bpy.app.timers.unregister(_disable_conflicts)
    _restore_conflicts()
    _unregister_keymaps()
    bpy.utils.unregister_class(MayaUnrealNavPreferences)
