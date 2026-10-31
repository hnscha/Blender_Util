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
DCC 기즈모: Move / Rotate / Scale 툴의 Blender 기즈모를 숨기고, 대기 중과 변환 중에 모양이 똑같은 기즈모를 그린다.
변환 중에도 X/Y/Z 가 모두 보이고 사라지거나 바뀌지 않는다 (현재 좌표계·피봇 기준). "Blender 기본" 모드에서는 쓰지 않는다.
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
    _sync_tool_gizmos()


def _update_gizmo_pref(_self, _context):
    _sync_tool_gizmos()


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
        name="DCC 기즈모 사용",
        description=(
            "Move/Rotate/Scale 툴의 Blender 기즈모 대신, 대기 중과 변환 중 모양이 같은 기즈모를 쓴다.\n"
            "변환 중에도 X/Y/Z 가 모두 계속 보인다"
        ),
        default=True,
        update=_update_gizmo_pref,
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


# -----------------------------------------------------------------------------
# DCC 기즈모
#
# 그리기는 한 곳(_draw_gizmo)에서만 한다. 대기 중이든 변환 중이든 같은 함수, 같은 크기·위치 계산이라
# 바뀌거나 깜빡이는 순간이 없다. 마우스로 잡는 부분은 투명한 GizmoGroup 이 같은 위치·크기로 맡는다.

_AXIS_COLORS = (
    (1.0, 0.2, 0.32, 1.0),
    (0.54, 0.86, 0.0, 1.0),
    (0.16, 0.56, 1.0, 1.0),
)
_HIGHLIGHT_COLOR = (1.0, 0.85, 0.2, 1.0)

_TOOL_KINDS = {
    "builtin.move": 'MOVE',
    "builtin.rotate": 'ROTATE',
    "builtin.scale": 'SCALE',
}
# DCC 기즈모를 쓰는 모드. 그 밖의 모드(커브·격자 편집 등)는 Blender 기즈모를 그대로 쓴다.
_GIZMO_MODES = {'OBJECT', 'EDIT_MESH', 'EDIT_ARMATURE', 'POSE'}

# 마우스가 올라간 축 (GizmoGroup 이 갱신), 변환을 시작할 때 잡은 축.
_state = {"hover": None, "drag": None, "running": None}
# 우리가 Blender 툴 기즈모를 숨긴 SpaceView3D 들 (as_pointer).
_hidden_spaces = set()


def _gizmo_enabled():
    prefs = _prefs()
    return prefs is not None and prefs.show_axis_overlay and prefs.nav_mode != 'BLENDER'


def _active_tool_kind(context):
    if context.mode not in _GIZMO_MODES:
        return None
    try:
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    except (AttributeError, TypeError, RuntimeError):
        return None
    if tool is None:
        return None
    return _TOOL_KINDS.get(tool.idname)


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


def _points_center(points, pivot):
    if not points:
        return None
    if pivot == 'BOUNDING_BOX_CENTER':
        lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
        hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
        return (lo + hi) * 0.5
    return sum(points, Vector()) / len(points)


def _selection_points(context):
    """선택된 대상의 월드 위치 목록과 활성 대상의 위치."""
    obj = context.active_object
    mode = context.mode
    pts = []
    active = None

    if mode == 'EDIT_MESH' and obj is not None and obj.type == 'MESH':
        bm = bmesh.from_edit_mesh(obj.data)
        mw = obj.matrix_world
        pts = [mw @ v.co for v in bm.verts if v.select]
        act = bm.select_history.active
        if isinstance(act, bmesh.types.BMVert):
            active = mw @ act.co
    elif mode == 'EDIT_ARMATURE' and obj is not None:
        mw = obj.matrix_world
        for b in obj.data.edit_bones:
            if b.select_head:
                pts.append(mw @ b.head)
            if b.select_tail:
                pts.append(mw @ b.tail)
        if obj.data.edit_bones.active is not None:
            active = mw @ obj.data.edit_bones.active.head
    elif mode == 'POSE' and obj is not None:
        pts = [pb.id_data.matrix_world @ pb.head for pb in (context.selected_pose_bones or [])]
        if context.active_pose_bone is not None:
            pb = context.active_pose_bone
            active = pb.id_data.matrix_world @ pb.head
    elif mode == 'OBJECT':
        pts = [o.matrix_world.translation.copy() for o in context.selected_objects]
        if obj is not None and obj.select_get():
            active = obj.matrix_world.translation.copy()
    return pts, active


def _selection_center(context):
    """Blender 기즈모와 같은 규칙으로 변환 중심(피봇)을 구한다."""
    pivot = context.scene.tool_settings.transform_pivot_point
    pts, active = _selection_points(context)
    if not pts:
        return None
    if pivot == 'CURSOR':
        return context.scene.cursor.location.copy()
    if pivot == 'ACTIVE_ELEMENT' and active is not None:
        return active
    return _points_center(pts, pivot)


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


def _gizmo_frame(context):
    """
    기즈모의 중심, 축 행렬, 월드 길이. 그리기와 GizmoGroup 이 같은 값을 쓴다.
    화면에서 길이가 항상 '기즈모 크기(px)' 가 되도록 월드 길이를 구한다.
    """
    prefs = _prefs()
    region = context.region
    rv3d = context.region_data
    if prefs is None or region is None or rv3d is None:
        return None
    center = _selection_center(context)
    if center is None:
        return None
    p2d = view3d_utils.location_3d_to_region_2d(region, rv3d, center)
    right = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
    q2d = view3d_utils.location_3d_to_region_2d(region, rv3d, center + right)
    if p2d is None or q2d is None or (q2d - p2d).length < 1e-6:
        return None
    length = prefs.axis_overlay_size / (q2d - p2d).length
    return center, _orientation_matrix(context), length


def _axis_basis(rot, i):
    """i 번 축을 +Z 로 삼는 정규 직교 기저 (side, up, axis)."""
    axis = rot.col[i].normalized()
    side = axis.orthogonal().normalized()
    up = axis.cross(side).normalized()
    return side, up, axis


def _current_kind(context):
    """지금 그릴 기즈모 종류: 변환 중이면 그 변환, 아니면 활성 툴."""
    running = _transform_running(context)
    if running is not None:
        if _state["running"] is None:
            # 변환이 막 시작됐다: 그때 마우스가 올라가 있던 축을 잡은 축으로 기억한다.
            _state["drag"] = _state["hover"]
        _state["running"] = running
        return running
    _state["running"] = None
    _state["drag"] = None
    return _active_tool_kind(context)


def _draw_gizmo():
    context = bpy.context
    if not _gizmo_enabled():
        return
    kind = _current_kind(context)
    if kind is None:
        return
    frame = _gizmo_frame(context)
    if frame is None:
        return
    center, rot, length = frame
    region = context.region
    rv3d = context.region_data

    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    tri_shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('ALPHA')

    shader.uniform_float("viewportSize", (region.width, region.height))

    def lines(points, color, width=2.5):
        shader.uniform_float("lineWidth", width)
        shader.uniform_float("color", color)
        batch_for_shader(shader, 'LINES', {"pos": points}).draw(shader)

    def tris(points, color):
        tri_shader.uniform_float("color", color)
        batch_for_shader(tri_shader, 'TRIS', {"pos": points}).draw(tri_shader)

    def circle(c, a, b, radius, segs=48):
        return [c + (a * math.cos(s / segs * 2.0 * math.pi) + b * math.sin(s / segs * 2.0 * math.pi)) * radius
                for s in range(segs)]

    def loop(pts):
        return [p for s in range(len(pts)) for p in (pts[s], pts[(s + 1) % len(pts)])]

    hot = _state["drag"] if _state["running"] is not None else _state["hover"]

    # 기본 기즈모와 같은 모양: 이동 = 화살표, 회전 = 축별 링, 스케일 = 끝이 네모인 축.
    for i in range(3):
        color = _HIGHLIGHT_COLOR if hot == i else _AXIS_COLORS[i]
        width = 3.5 if hot == i else 2.5
        side, up, axis = _axis_basis(rot, i)

        if kind == 'ROTATE':
            lines(loop(circle(center, side, up, length)), color, width)
            continue

        tip = center + axis * length
        lines([center, tip], color, width)

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
        lines(loop(circle(center, view_x, view_y, length * 1.2)), (1.0, 1.0, 1.0, 0.6))

    gpu.state.blend_set('NONE')


class VIEW3D_GGT_dcc_navigation(bpy.types.GizmoGroup):
    """
    마우스로 잡는 부분만 맡는 투명 기즈모. 위치·축·크기는 _gizmo_frame 과 같아서
    보이는 기즈모(_draw_gizmo)와 잡히는 영역이 일치한다.
    """
    bl_idname = "VIEW3D_GGT_dcc_navigation"
    bl_label = "DCC Navigation Gizmo"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT'}

    @classmethod
    def poll(cls, context):
        return _gizmo_enabled() and _active_tool_kind(context) is not None

    def setup(self, _context):
        self.handles = {}
        for kind in ('MOVE', 'ROTATE', 'SCALE'):
            items = []
            for i in range(3):
                if kind == 'ROTATE':
                    gz = self.gizmos.new("GIZMO_GT_dial_3d")
                else:
                    gz = self.gizmos.new("GIZMO_GT_arrow_3d")
                    gz.draw_style = 'BOX' if kind == 'SCALE' else 'NORMAL'
                gz.use_draw_scale = False
                gz.color = _AXIS_COLORS[i][:3]
                gz.color_highlight = _HIGHLIGHT_COLOR[:3]
                # 보이는 모양은 _draw_gizmo 가 그린다. 이 기즈모는 잡는 영역만 제공한다.
                gz.alpha = 0.0
                gz.alpha_highlight = 0.0
                items.append(gz)
            self.handles[kind] = items
        self.bound = None

    def _bind(self, orient):
        for kind, items in self.handles.items():
            for i, gz in enumerate(items):
                axis = [False, False, False]
                axis[i] = True
                if kind == 'MOVE':
                    props = gz.target_set_operator("transform.translate")
                elif kind == 'ROTATE':
                    props = gz.target_set_operator("transform.rotate")
                    props.orient_axis = "XYZ"[i]
                else:
                    props = gz.target_set_operator("transform.resize")
                props.constraint_axis = axis
                props.orient_type = orient
                props.release_confirm = True
        self.bound = orient

    def draw_prepare(self, context):
        kind = _active_tool_kind(context)
        frame = _gizmo_frame(context) if kind is not None else None
        orient = context.scene.transform_orientation_slots[0].type
        if self.bound != orient:
            self._bind(orient)

        hover = None
        for k, items in self.handles.items():
            for i, gz in enumerate(items):
                visible = frame is not None and k == kind
                gz.hide = not visible
                if not visible:
                    continue
                center, rot, length = frame
                side, up, axis = _axis_basis(rot, i)
                basis = Matrix((side, up, axis)).transposed().to_4x4()
                gz.matrix_basis = Matrix.Translation(center) @ basis @ Matrix.Scale(length, 4)
                if gz.is_highlight:
                    hover = i
        _state["hover"] = hover


# -----------------------------------------------------------------------------
# Blender 툴 기즈모 숨기기

def _iter_view3d_spaces():
    """(window, space) 쌍."""
    wm = bpy.context.window_manager
    if wm is None:
        return
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        yield win, space


def _window_wants_hide(win):
    if not _gizmo_enabled():
        return False
    try:
        with bpy.context.temp_override(window=win):
            return _active_tool_kind(bpy.context) is not None
    except (TypeError, RuntimeError, AttributeError):
        return False


def _sync_tool_gizmos():
    """
    DCC 기즈모를 쓰는 동안 Move/Rotate/Scale 툴일 때만 Blender 툴 기즈모를 숨긴다.
    다른 툴(돌출, 베벨 등)의 기즈모는 그대로 보이게, 우리가 숨긴 것만 되돌린다.
    """
    wants = {}
    for win, space in list(_iter_view3d_spaces()):
        if win not in wants:
            wants[win] = _window_wants_hide(win)
        want_hide = wants[win]
        key = space.as_pointer()
        if want_hide:
            if space.show_gizmo_tool:
                space.show_gizmo_tool = False
                _hidden_spaces.add(key)
        elif key in _hidden_spaces:
            space.show_gizmo_tool = True
            _hidden_spaces.discard(key)


def _tool_gizmo_timer():
    _sync_tool_gizmos()
    return 0.2


def _restore_tool_gizmos():
    for _win, space in _iter_view3d_spaces():
        if space.as_pointer() in _hidden_spaces:
            space.show_gizmo_tool = True
    _hidden_spaces.clear()


_classes = (
    DCCNavigationPreferences,
    VIEW3D_PT_dcc_navigation,
    VIEW3D_GGT_dcc_navigation,
)


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
            _draw_gizmo, (), 'WINDOW', 'POST_VIEW',
        )
        bpy.app.timers.register(_tool_gizmo_timer, first_interval=0.5, persistent=True)


def unregister():
    global _draw_handle
    if bpy.app.timers.is_registered(_tool_gizmo_timer):
        bpy.app.timers.unregister(_tool_gizmo_timer)
    _restore_tool_gizmos()
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
