# SPDX-License-Identifier: GPL-3.0-or-later
"""
DCC Navigation — 뷰포트 조작 모드 전환 애드온 (Blender 기본 / Maya·Unreal / 3ds Max).

  Maya·Unreal  Alt + 왼쪽 / 가운데 / 오른쪽 드래그 = 회전 / 이동 / 줌
  3ds Max      가운데 드래그 = 이동, Alt + 가운데 = 회전, Ctrl + Alt + 가운데(또는 휠) = 줌
  공통         Q / W / E / R = 선택(박스) / 이동 / 회전 / 스케일 툴
               1 / 2 (숫자 윗줄) = 좌표계 World(Global) / Local  (넘패드는 건드리지 않음)
  Blender 기본 애드온 키를 모두 빼고 꺼 둔 기본 단축키를 되살린다.

모드는 3D 뷰포트 N 패널의 "DCC Nav" 탭이나 애드온 설정에서 바꾸고, 환경 설정에 저장되어 재시작 후에도 유지된다.
Object / Edit / Pose 모드, Layout / Modeling / Animating 등 모든 워크스페이스에서 같다.
선택한 모드와 같은 키를 쓰는 Blender 기본 단축키만 활성 키맵에서 꺼 두고, 모드를 바꾸거나 애드온을 끄면 되돌린다.
이동/회전/스케일 중에는 Blender 가 숨기는 기즈모 대신 같은 모양의 X/Y/Z 기즈모를 그린다 (현재 좌표계 기준).
"""

import math
import time

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

_ORIENT_PATH = "scene.transform_orientation_slots[0].type"
_ORIENT_KEYS = (
    ("ONE", 'GLOBAL'),
    ("TWO", 'LOCAL'),
)

# 모드별 뷰 조작 키: (오퍼레이터, 마우스 버튼, alt, ctrl). 휠 줌은 Blender 기본을 그대로 쓴다.
_MODES = {
    'BLENDER': {
        "nav": (),
        "tools": False,
    },
    'MAYA': {
        "nav": (
            ("view3d.rotate", "LEFTMOUSE", True, False),
            ("view3d.move", "MIDDLEMOUSE", True, False),
            ("view3d.zoom", "RIGHTMOUSE", True, False),
        ),
        "tools": True,
    },
    'MAX': {
        "nav": (
            ("view3d.move", "MIDDLEMOUSE", False, False),
            ("view3d.rotate", "MIDDLEMOUSE", True, False),
            ("view3d.zoom", "MIDDLEMOUSE", True, True),
        ),
        "tools": True,
    },
}

_MODE_ITEMS = (
    ('BLENDER', "Blender 기본", "Blender 기본 조작 (애드온 키 없음)"),
    ('MAYA', "Maya / Unreal", "Alt + 왼쪽/가운데/오른쪽 드래그 = 회전/이동/줌, QWER, 1/2"),
    ('MAX', "3ds Max", "가운데 드래그 = 이동, Alt+가운데 = 회전, Ctrl+Alt+가운데 = 줌, QWER, 1/2"),
)

_MODE_HELP = {
    'BLENDER': ("Blender 기본 조작",),
    'MAYA': (
        "Alt + 왼쪽 / 가운데 / 오른쪽 드래그 : 회전 / 이동 / 줌",
        "Q / W / E / R : 선택 / 이동 / 회전 / 스케일",
        "1 / 2 : 좌표계 World / Local",
    ),
    'MAX': (
        "가운데 드래그 : 이동,  Alt + 가운데 : 회전",
        "Ctrl + Alt + 가운데 또는 휠 : 줌",
        "Q / W / E / R : 선택 / 이동 / 회전 / 스케일",
        "1 / 2 : 좌표계 World / Local",
    ),
}

# 3D 뷰포트에서 이 애드온 키보다 먼저/같이 처리될 수 있는 키맵. 여기서 같은 키를 쓰는 기본 항목을 끈다.
_CONFLICT_KEYMAPS = (
    "Window",
    "Screen",
    "Frames",
    "Object Non-modal",
    "3D View Generic",
    "3D View",
) + _TOOL_KEYMAPS

# (keymap, keymap_item) 목록. 모드를 바꾸거나 unregister 할 때 그대로 제거한다.
_addon_keymaps = []
# 현재 모드가 추가한 항목의 서명. 활성 키맵에 합쳐진 사본을 알아보는 데 쓴다.
_own_sigs = set()
_draw_handle = None
# 축 표시 디버그용: 최근에 감지한 (시각, 모달 오퍼레이터 목록, 종류).
_last_seen = []


# -----------------------------------------------------------------------------
# 키 조합

def _mode_combos(mode):
    """모드가 쓰는 (키, alt, ctrl) 조합. shift/oskey 는 쓰지 않는다."""
    spec = _MODES[mode]
    combos = {(mouse, alt, ctrl) for _, mouse, alt, ctrl in spec["nav"]}
    if spec["tools"]:
        combos |= {(key, False, False) for key, _ in _TOOL_KEYS}
        combos |= {(key, False, False) for key, _ in _ORIENT_KEYS}
    return combos


_ALL_COMBOS = set().union(*(_mode_combos(m) for m in _MODES))


def _item_prop(kmi):
    if kmi.idname == "wm.tool_set_by_id":
        return kmi.properties.name
    if kmi.idname == "wm.context_set_enum":
        return "{:s}={:s}".format(kmi.properties.data_path, kmi.properties.value)
    return ""


def _sig(km_name, kmi):
    return (km_name, kmi.idname, kmi.type, bool(kmi.alt), bool(kmi.ctrl), bool(kmi.shift), _item_prop(kmi))


def _combo_of(kmi):
    if kmi.key_modifier != 'NONE' or kmi.shift or kmi.oskey or kmi.hyper or kmi.any:
        return None
    return (kmi.type, bool(kmi.alt), bool(kmi.ctrl))


def _key_text(kmi):
    mods = ("Ctrl+" if kmi.ctrl else "") + ("Alt+" if kmi.alt else "") + ("Shift+" if kmi.shift else "")
    return mods + kmi.type + "/" + kmi.value


# -----------------------------------------------------------------------------
# 키맵

def _register_keymaps(mode):
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        # 백그라운드 모드에는 애드온 키컨피그가 없다.
        return
    spec = _MODES[mode]

    def add(km, idname, key, **kw):
        props = kw.pop("props", {})
        kmi = km.keymap_items.new(idname, key, 'PRESS', **kw)
        for name, value in props.items():
            setattr(kmi.properties, name, value)
        _addon_keymaps.append((km, kmi))
        _own_sigs.add(_sig(km.name, kmi))

    if spec["nav"] or spec["tools"]:
        km = kc.keymaps.new(name="3D View", space_type='VIEW_3D')
        for idname, mouse, alt, ctrl in spec["nav"]:
            add(km, idname, mouse, alt=alt, ctrl=ctrl)
        if spec["tools"]:
            for key, orient in _ORIENT_KEYS:
                add(km, "wm.context_set_enum", key, props={"data_path": _ORIENT_PATH, "value": orient})

    if spec["tools"]:
        for km_name in _TOOL_KEYMAPS:
            km = kc.keymaps.new(name=km_name, space_type='EMPTY')
            for key, tool in _TOOL_KEYS:
                add(km, "wm.tool_set_by_id", key, props={"name": tool})


def _unregister_keymaps():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _addon_keymaps.clear()
    _own_sigs.clear()


def _all_own_like_sigs(kc_user):
    """활성 키맵에서 어떤 모드의 애드온 항목과도 같은 모양인 항목의 서명 (키맵별)."""
    out = {}
    for km_name in ("3D View",) + _TOOL_KEYMAPS:
        km = kc_user.keymaps.get(km_name)
        if km is None:
            continue
        out[km_name] = {_sig(km_name, kmi) for kmi in km.keymap_items}
    return out


def _sync_user_keymaps():
    """
    활성(user) 키맵에 애드온 항목이 제대로 합쳐졌는지 확인한다.
    사용자 수정이 있는 키맵은 실행 중에 애드온 항목을 늦게/안 합치는 경우가 있어,
    어긋난 키맵만 기본값으로 되돌린 뒤 다시 합친다 (꺼 둔 기본 키는 이후 _disable_conflicts 가 다시 끈다).
    """
    wm = bpy.context.window_manager
    wm.keyconfigs.update()
    kc_user = wm.keyconfigs.user
    if kc_user is None:
        return
    stale = []
    present = _all_own_like_sigs(kc_user)
    possible = _possible_own_sigs()
    for km_name, sigs in present.items():
        want = {s for s in _own_sigs if s[0] == km_name}
        have = {s for s in sigs if s in possible}
        if want != have:
            stale.append(km_name)
    for km_name in stale:
        kc_user.keymaps[km_name].restore_to_default()
    if stale:
        wm.keyconfigs.update()


def _possible_own_sigs():
    """모든 모드의 애드온 항목 서명 (키맵에 남은 지난 모드 항목을 찾는 데 쓴다)."""
    sigs = set()
    for spec in _MODES.values():
        for idname, mouse, alt, ctrl in spec["nav"]:
            sigs.add(("3D View", idname, mouse, alt, ctrl, False, ""))
        if spec["tools"]:
            for key, orient in _ORIENT_KEYS:
                sigs.add(("3D View", "wm.context_set_enum", key, False, False, False,
                          "{:s}={:s}".format(_ORIENT_PATH, orient)))
            for km_name in _TOOL_KEYMAPS:
                for key, tool in _TOOL_KEYS:
                    sigs.add((km_name, "wm.tool_set_by_id", key, False, False, False, tool))
    return sigs


def _conflict_items(combos, active_only=True):
    kc = bpy.context.window_manager.keyconfigs.user
    if kc is None:
        return []
    found = []
    for km_name in _CONFLICT_KEYMAPS:
        km = kc.keymaps.get(km_name)
        if km is None:
            continue
        for kmi in km.keymap_items:
            if _combo_of(kmi) not in combos or _sig(km_name, kmi) in _own_sigs:
                continue
            if kmi.active != active_only:
                continue
            found.append((km, kmi))
    return found


def _disable_conflicts():
    mode = _current_mode()
    for _km, kmi in _conflict_items(_mode_combos(mode), active_only=True):
        kmi.active = False
    return None  # 타이머 1회 실행.


def _restore_conflicts():
    """어떤 모드에서든 꺼 둘 수 있는 기본 항목을 모두 기본값으로 되돌린다."""
    possible = _possible_own_sigs()
    for km, kmi in _conflict_items(_ALL_COMBOS, active_only=False):
        if _sig(km.name, kmi) in possible:
            continue
        try:
            km.restore_item_to_default(kmi)
        except (ReferenceError, RuntimeError):
            kmi.active = True


def _apply_mode(mode):
    _restore_conflicts()
    _unregister_keymaps()
    _register_keymaps(mode)
    _sync_user_keymaps()
    _disable_conflicts()


def _startup_apply():
    # 시작 직후에는 활성 키맵이 아직 다 만들어지지 않았을 수 있어 한 번 더 맞춘다.
    _sync_user_keymaps()
    _disable_conflicts()
    return None


# -----------------------------------------------------------------------------
# 환경 설정 / UI

def _prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _current_mode():
    prefs = _prefs()
    return prefs.nav_mode if prefs is not None else 'MAYA'


def _update_nav_mode(self, _context):
    _apply_mode(self.nav_mode)


def _draw_mode_ui(layout, prefs):
    col = layout.column(align=True)
    col.prop(prefs, "nav_mode", expand=True)
    box = layout.box()
    col = box.column(align=True)
    for line in _MODE_HELP[prefs.nav_mode]:
        col.label(text=line)


class DCCNavigationPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    nav_mode: bpy.props.EnumProperty(
        name="조작 모드",
        items=_MODE_ITEMS,
        default='MAYA',
        update=_update_nav_mode,
    )
    show_axis_overlay: bpy.props.BoolProperty(
        name="변환 중 기즈모 유지",
        description="이동/회전/스케일 중에도 X/Y/Z 기즈모(화살표/링/스케일 핸들)를 계속 그린다",
        default=True,
    )
    axis_overlay_size: bpy.props.IntProperty(
        name="기즈모 크기 (px)",
        default=80,
        min=20,
        max=400,
    )

    def draw(self, context):
        layout = self.layout
        inputs = context.preferences.inputs

        _draw_mode_ui(layout, self)

        if self.nav_mode == 'MAYA' and inputs.use_mouse_emulate_3_button:
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

        disabled = _conflict_items(_mode_combos(self.nav_mode), active_only=False)
        if disabled:
            box = layout.box()
            box.label(text="겹쳐서 꺼 둔 기본 단축키 ({:d})".format(len(disabled)))
            col = box.column(align=True)
            for km, kmi in disabled:
                col.label(text="{:s} : {:s}  ({:s})".format(km.name, _key_text(kmi), kmi.name or kmi.idname))


class VIEW3D_PT_dcc_navigation(bpy.types.Panel):
    bl_label = "DCC Navigation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "DCC Nav"

    def draw(self, _context):
        prefs = _prefs()
        if prefs is None:
            return
        layout = self.layout
        _draw_mode_ui(layout, prefs)
        row = layout.row()
        row.prop(prefs, "show_axis_overlay")


_classes = (
    DCCNavigationPreferences,
    VIEW3D_PT_dcc_navigation,
)


# -----------------------------------------------------------------------------
# 변환 중 축 표시

_AXIS_COLORS = (
    (1.0, 0.2, 0.32, 1.0),
    (0.54, 0.86, 0.0, 1.0),
    (0.16, 0.56, 1.0, 1.0),
)


def _op_kind(op):
    idname = op.bl_idname
    if idname.startswith("TRANSFORM_OT_"):
        name = idname[len("TRANSFORM_OT_"):]
    elif idname.startswith("transform."):
        name = idname[len("transform."):]
    else:
        return None
    if name in {"rotate", "trackball"}:
        return 'ROTATE'
    if name in {"resize", "shrink_fatten", "skin_resize"}:
        return 'SCALE'
    if name == "transform":
        # transform.transform (Transform 툴 등): mode 속성에 실제 변환 종류가 있다.
        try:
            mode = op.properties.mode
        except (AttributeError, ReferenceError):
            mode = ""
        if mode in {'ROTATION', 'TRACKBALL'}:
            return 'ROTATE'
        if mode in {'RESIZE', 'SKIN_RESIZE'}:
            return 'SCALE'
    return 'MOVE'


def _transform_running(context):
    """실행 중인 변환 종류 ('MOVE' / 'ROTATE' / 'SCALE') 를 돌려준다. 변환 중이 아니면 None."""
    win = context.window
    if win is None:
        return None
    ops = list(win.modal_operators)
    for op in ops:
        kind = _op_kind(op)
        if kind is not None:
            if not _last_seen or _last_seen[-1][2] != kind:
                _last_seen.append((time.time(), [o.bl_idname for o in ops], kind))
                del _last_seen[:-20]
            return kind
    return None


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
    kind = _transform_running(context)
    if kind is None:
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

    shader.uniform_float("viewportSize", (region.width, region.height))
    shader.uniform_float("lineWidth", 2.5)

    def lines(points, color):
        shader.uniform_float("color", color)
        batch_for_shader(shader, 'LINES', {"pos": points}).draw(shader)

    def tris(points, color):
        tri_shader.uniform_float("color", color)
        batch_for_shader(tri_shader, 'TRIS', {"pos": points}).draw(tri_shader)

    def circle(c, a, b, radius, segs=48):
        pts = [c + (a * math.cos(s / segs * 2.0 * math.pi) + b * math.sin(s / segs * 2.0 * math.pi)) * radius
               for s in range(segs)]
        return pts

    # 기본 기즈모와 같은 모양: 이동 = 화살표, 회전 = 축별 링, 스케일 = 끝이 네모인 축.
    for i in range(3):
        color = _AXIS_COLORS[i]
        axis = rot.col[i].normalized()
        side = axis.orthogonal().normalized()
        up = axis.cross(side).normalized()

        if kind == 'ROTATE':
            ring = circle(center, side, up, length)
            lines([p for s in range(len(ring)) for p in (ring[s], ring[(s + 1) % len(ring)])], color)
            continue

        tip = center + axis * length
        lines([center, tip], color)

        if kind == 'SCALE':
            h = length * 0.06
            corners = [tip + (side * sx + up * sy + axis * sz) * h
                       for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
            faces = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
            tris([corners[j] for f in faces for j in (f[0], f[1], f[2], f[0], f[2], f[3])], color)
        else:
            base = tip - axis * (length * 0.18)
            ring = circle(base, side, up, length * 0.06, segs=12)
            tris([p for s in range(12) for p in (tip, ring[s], ring[(s + 1) % 12])], color)

    if kind == 'ROTATE':
        # 화면 방향 바깥 링 (기본 회전 기즈모의 흰 링).
        view_x = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
        view_y = rv3d.view_rotation @ Vector((0.0, 1.0, 0.0))
        ring = circle(center, view_x, view_y, length * 1.2)
        lines([p for s in range(len(ring)) for p in (ring[s], ring[(s + 1) % len(ring)])], (1.0, 1.0, 1.0, 0.6))

    gpu.state.blend_set('NONE')


# -----------------------------------------------------------------------------
# 등록

def register():
    global _draw_handle
    for cls in _classes:
        bpy.utils.register_class(cls)
    mode = _current_mode()
    _register_keymaps(mode)
    if bpy.context.window_manager.keyconfigs.user is not None:
        _disable_conflicts()
    bpy.app.timers.register(_startup_apply, first_interval=1.0)
    if not bpy.app.background:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_axis_overlay, (), 'WINDOW', 'POST_VIEW',
        )


def unregister():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if bpy.app.timers.is_registered(_startup_apply):
        bpy.app.timers.unregister(_startup_apply)
    _restore_conflicts()
    _unregister_keymaps()
    # 활성 키맵에 남은 애드온 항목 사본을 바로 걷어낸다.
    if bpy.context.window_manager.keyconfigs.user is not None:
        bpy.context.window_manager.keyconfigs.update()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
