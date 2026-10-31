# SPDX-License-Identifier: GPL-3.0-or-later
"""
DCC Navigation — 뷰포트 조작 모드 전환 애드온 (Blender 기본 / Maya·Unreal / 3ds Max).

조작 모드 (뷰포트 마우스 조작만 바뀐다)
  Blender 기본  Blender 그대로
  Maya·Unreal  Alt + 왼쪽 / 가운데 / 오른쪽 드래그 = 회전 / 이동 / 줌
  3ds Max      가운데 드래그 = 이동, Alt + 가운데 = 회전, Ctrl + Alt + 가운데(또는 휠) = 줌

단축키 (모드와 따로 켜고 끈다)
  Q / W / E / R = 선택(박스) / 이동 / 회전 / 스케일 툴
  1 / 2 (숫자 윗줄) = 좌표계 World(Global) / Local  (넘패드는 건드리지 않음)

모드·단축키·기즈모는 3D 뷰포트 N 패널의 "DCC Nav" 탭이나 애드온 설정에서 바꾸고, 재시작 후에도 유지된다.
Object / Edit / Pose 모드, Layout / Modeling / Animating 등 모든 워크스페이스에서 같다.
지금 쓰는 키와 겹치는 Blender 기본 단축키만 활성 키맵에서 꺼 두고, 설정을 바꾸거나 애드온을 끄면 되돌린다.
DCC 기즈모: Move / Rotate / Scale 툴의 Blender 기즈모를 숨기고, 대기 중과 변환 중에 모양이 똑같은 기즈모를 그린다.
변환 중에도 X/Y/Z 가 모두 보이고 사라지거나 바뀌지 않는다 (현재 좌표계·피봇 기준).
"""

import math
import time

import blf
import bmesh
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Quaternion, Vector
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
# 모드는 뷰포트 마우스 조작만 바꾼다. 단축키(QWER, 1/2)는 모드와 따로 켜고 끈다.
_MODES = {
    'BLENDER': (),
    'MAYA': (
        ("view3d.rotate", "LEFTMOUSE", True, False),
        ("view3d.move", "MIDDLEMOUSE", True, False),
        ("view3d.zoom", "RIGHTMOUSE", True, False),
    ),
    'MAX': (
        ("view3d.move", "MIDDLEMOUSE", False, False),
        ("view3d.rotate", "MIDDLEMOUSE", True, False),
        ("view3d.zoom", "MIDDLEMOUSE", True, True),
    ),
}

_MODE_ITEMS = (
    ('BLENDER', "Blender 기본", "Blender 기본 뷰포트 조작"),
    ('MAYA', "Maya / Unreal", "Alt + 왼쪽/가운데/오른쪽 드래그 = 회전/이동/줌"),
    ('MAX', "3ds Max", "가운데 드래그 = 이동, Alt+가운데 = 회전, Ctrl+Alt+가운데 = 줌"),
)

_MODE_HELP = {
    'BLENDER': (
        "가운데 드래그 : 회전,  Shift + 가운데 : 이동",
        "Ctrl + 가운데 또는 휠 : 줌",
    ),
    'MAYA': (
        "Alt + 왼쪽 / 가운데 / 오른쪽 드래그 : 회전 / 이동 / 줌",
    ),
    'MAX': (
        "가운데 드래그 : 이동,  Alt + 가운데 : 회전",
        "Ctrl + Alt + 가운데 또는 휠 : 줌",
    ),
}

_HOTKEY_HELP = (
    "Q / W / E / R : 선택 / 이동 / 회전 / 스케일",
    "1 / 2 : 좌표계 World / Local",
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

# (keymap, keymap_item) 목록. 모드를 바꾸거나 unregister 할 때 그대로 제거한다.
_addon_keymaps = []
# 현재 모드가 추가한 항목의 서명. 활성 키맵에 합쳐진 사본을 알아보는 데 쓴다.
_own_sigs = set()
_draw_handle = None
# 축 표시 디버그용: 최근에 감지한 (시각, 모달 오퍼레이터 목록, 종류).
_last_seen = []


# -----------------------------------------------------------------------------
# 키 조합

_HOTKEY_COMBOS = {(key, False, False) for key, _ in _TOOL_KEYS} | {(key, False, False) for key, _ in _ORIENT_KEYS}


def _mode_combos(mode, hotkeys):
    """지금 쓰는 (키, alt, ctrl) 조합. shift/oskey 는 쓰지 않는다."""
    combos = {(mouse, alt, ctrl) for _, mouse, alt, ctrl in _MODES[mode]}
    if hotkeys:
        combos |= _HOTKEY_COMBOS
    return combos


def _active_combos():
    return _mode_combos(_current_mode(), _hotkeys_on())


_ALL_COMBOS = set().union(*(_mode_combos(m, True) for m in _MODES))


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

def _register_keymaps(mode, hotkeys):
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        # 백그라운드 모드에는 애드온 키컨피그가 없다.
        return
    nav = _MODES[mode]

    def add(km, idname, key, **kw):
        props = kw.pop("props", {})
        kmi = km.keymap_items.new(idname, key, 'PRESS', **kw)
        for name, value in props.items():
            setattr(kmi.properties, name, value)
        _addon_keymaps.append((km, kmi))
        _own_sigs.add(_sig(km.name, kmi))

    if nav or hotkeys:
        km = kc.keymaps.new(name="3D View", space_type='VIEW_3D')
        for idname, mouse, alt, ctrl in nav:
            add(km, idname, mouse, alt=alt, ctrl=ctrl)
        if hotkeys:
            for key, orient in _ORIENT_KEYS:
                add(km, "wm.context_set_enum", key, props={"data_path": _ORIENT_PATH, "value": orient})

    if hotkeys:
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
    for nav in _MODES.values():
        for idname, mouse, alt, ctrl in nav:
            sigs.add(("3D View", idname, mouse, alt, ctrl, False, ""))
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
    for _km, kmi in _conflict_items(_active_combos(), active_only=True):
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


def _apply_mode(mode, hotkeys):
    _restore_conflicts()
    _unregister_keymaps()
    _register_keymaps(mode, hotkeys)
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


def _hotkeys_on():
    prefs = _prefs()
    return prefs.use_hotkeys if prefs is not None else True


def _update_keys(self, _context):
    _apply_mode(self.nav_mode, self.use_hotkeys)


def _update_gizmo_pref(_self, _context):
    _sync_tool_gizmos()


def _draw_mode_ui(layout, prefs):
    layout.label(text="뷰포트 조작 모드")
    col = layout.column(align=True)
    col.prop(prefs, "nav_mode", expand=True)
    box = layout.box()
    col = box.column(align=True)
    for line in _MODE_HELP[prefs.nav_mode]:
        col.label(text=line)

    layout.separator()
    layout.prop(prefs, "use_hotkeys")
    box = layout.box()
    box.active = prefs.use_hotkeys
    col = box.column(align=True)
    for line in _HOTKEY_HELP:
        col.label(text=line)

    layout.separator()
    layout.prop(prefs, "show_axis_overlay")


class DCCNavigationPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    nav_mode: bpy.props.EnumProperty(
        name="조작 모드",
        description="뷰포트 마우스 조작 방식 (단축키는 아래에서 따로 켜고 끈다)",
        items=_MODE_ITEMS,
        default='MAYA',
        update=_update_keys,
    )
    use_hotkeys: bpy.props.BoolProperty(
        name="단축키 (QWER, 1/2)",
        description="Q/W/E/R 툴 전환과 1/2 World/Local 전환. 조작 모드와 상관없이 따로 켜고 끈다",
        default=True,
        update=_update_keys,
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

        row = layout.row()
        row.active = self.show_axis_overlay
        row.prop(self, "axis_overlay_size")

        box = layout.box()
        box.label(text="Blender 내비게이션 설정 (선택)")
        box.prop(inputs, "use_zoom_to_mouse")
        box.prop(inputs, "use_rotate_around_active")

        disabled = _conflict_items(_mode_combos(self.nav_mode, self.use_hotkeys), active_only=False)
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
        _draw_mode_ui(self.layout, prefs)


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
_state = {
    "hover": None, "drag": None, "running": None,
    "start_q": None, "start_m3": None, "start_rot": None,
    "angle": None, "scale": None,
    # 애드온 자체 변환(DCCNAV_OT_gizmo_transform) 진행 상황. 그 밖에는 None.
    "op": None,
}
_text_handle = None
# 우리가 Blender 툴 기즈모를 숨긴 SpaceView3D 들 (as_pointer).
_hidden_spaces = set()


def _gizmo_enabled():
    prefs = _prefs()
    return prefs is not None and prefs.show_axis_overlay


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
    if idname in {"DCCNAV_OT_gizmo_transform", "dccnav.gizmo_transform"}:
        try:
            return op.properties.kind
        except (AttributeError, ReferenceError):
            return 'MOVE'
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


def _active_matrix3(context):
    """회전량·스케일량 표시에 쓰는 활성 대상의 월드 3x3 행렬. 편집 모드는 None."""
    obj = context.active_object
    if obj is None:
        return None
    if context.mode == 'OBJECT':
        return obj.matrix_world.to_3x3()
    if context.mode == 'POSE' and context.active_pose_bone is not None:
        return (obj.matrix_world @ context.active_pose_bone.matrix).to_3x3()
    return None


def _active_quat(context):
    m3 = _active_matrix3(context)
    return m3.to_quaternion() if m3 is not None else None


def _scale_factors(context):
    """변환 시작부터 지금까지 시작 기저의 각 축 방향으로 늘어난 배율 (x, y, z). 모르면 None."""
    start_m3 = _state["start_m3"]
    start_rot = _state["start_rot"]
    cur = _active_matrix3(context)
    if start_m3 is None or start_rot is None or cur is None:
        return None
    try:
        delta = cur @ start_m3.inverted()
    except ValueError:
        return None
    return tuple((delta @ start_rot.col[i].normalized()).length for i in range(3))


def _current_kind(context):
    """지금 그릴 기즈모 종류: 변환 중이면 그 변환, 아니면 활성 툴."""
    running = _transform_running(context)
    if running is not None:
        if _state["running"] is None:
            # 변환이 막 시작됐다: 그때 마우스가 올라가 있던 축을 잡은 축으로 기억하고,
            # 회전량(Maya/Max 의 각도 부채꼴·숫자) 계산 기준을 잡아 둔다.
            if _state["drag"] is None:
                _state["drag"] = _state["hover"]
            _state["start_q"] = _active_quat(context)
            _state["start_m3"] = _active_matrix3(context)
            _state["start_rot"] = _orientation_matrix(context)
        _state["running"] = running
        return running
    _state["running"] = None
    _state["drag"] = None
    _state["start_q"] = None
    _state["start_m3"] = None
    _state["start_rot"] = None
    _state["angle"] = None
    _state["scale"] = None
    return _active_tool_kind(context)


def _rotation_progress(context, hot):
    """
    변환 시작부터 지금까지의 회전: (축 번호, 시작 기저의 축 벡터, 각도 라디안).
    잡은 축이 없으면(키로 시작한 자유 회전 등) 회전축과 가장 가까운 기저 축을 쓴다.
    """
    start_q = _state["start_q"]
    start_rot = _state["start_rot"]
    cur = _active_quat(context)
    if start_q is None or start_rot is None or cur is None:
        return None
    delta = cur @ start_q.inverted()
    angle = delta.angle
    if angle < 1e-6:
        i = hot if hot is not None else 0
        return i, start_rot.col[i].normalized(), 0.0
    d_axis = delta.axis
    if hot is None:
        dots = [abs(d_axis.dot(start_rot.col[j].normalized())) for j in range(3)]
        i = dots.index(max(dots))
    else:
        i = hot
    a = start_rot.col[i].normalized()
    theta = angle if d_axis.dot(a) >= 0.0 else -angle
    if theta > math.pi:
        theta -= 2.0 * math.pi
    elif theta < -math.pi:
        theta += 2.0 * math.pi
    return i, a, theta


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

    # Maya/Max 처럼 스케일 중에는 핸들이 배율만큼 늘었다 줄었다 하고, 바뀌는 축이 강조된다.
    own = _state["op"]
    if _state["running"] != 'SCALE':
        factors = None
    elif own is not None and own["kind"] == 'SCALE':
        factors = own["factors"]
    else:
        factors = _scale_factors(context)
    _state["scale"] = (factors, center.copy()) if factors is not None else None

    # 기본 기즈모와 같은 모양: 이동 = 화살표, 회전 = 축별 링, 스케일 = 끝이 네모인 축.
    for i in range(3):
        scaling = factors is not None and abs(factors[i] - 1.0) > 1e-3
        color = _HIGHLIGHT_COLOR if hot == i or scaling else _AXIS_COLORS[i]
        width = 3.5 if hot == i or scaling else 2.5
        side, up, axis = _axis_basis(rot, i)

        if kind == 'ROTATE':
            lines(loop(circle(center, side, up, length)), color, width)
            continue

        axis_len = length
        if factors is not None:
            axis_len = length * min(max(factors[i], 0.05), 5.0)
            if scaling:
                # 원래 길이 자리를 흐리게 남겨 얼마나 늘었는지 보이게 한다.
                ghost = center + axis * length
                lines([center, ghost], _AXIS_COLORS[i][:3] + (0.35,), 1.5)
        tip = center + axis * axis_len
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

        # Maya/Max 처럼 돌린 만큼을 부채꼴로 채워 보여 준다 (시작 위치 기준).
        r0 = None
        if _state["running"] != 'ROTATE':
            progress = None
        elif own is not None and own["kind"] == 'ROTATE':
            # 애드온 자체 회전: 각도와 잡은 지점(부채꼴 시작)을 연산자가 알려 준다.
            progress = (own["axis"], own["axis_vec"], own["value"])
            r0 = own["r0"]
        else:
            progress = _rotation_progress(context, hot)
        _state["angle"] = None
        if progress is not None:
            i, a, theta = progress
            _state["angle"] = (i, math.degrees(theta), center.copy())
            if r0 is None:
                r0 = view_y - a * view_y.dot(a)
                if r0.length < 1e-6:
                    r0 = a.orthogonal()
            r0 = r0.normalized()
            b = a.cross(r0)
            steps = max(2, int(abs(theta) / (2.0 * math.pi) * 64))
            arc = [center + (r0 * math.cos(theta * s / steps) + b * math.sin(theta * s / steps)) * length
                   for s in range(steps + 1)]
            if abs(theta) > 1e-4:
                fill = _AXIS_COLORS[i][:3] + (0.25,)
                tris([p for s in range(steps) for p in (center, arc[s], arc[s + 1])], fill)
            lines([center, arc[0], center, arc[-1]], _HIGHLIGHT_COLOR, 2.0)

    gpu.state.blend_set('NONE')


def _draw_angle_text():
    """회전 중 각도, 스케일 중 배율 숫자 (Max 처럼 기즈모 옆에 표시)."""
    if not _gizmo_enabled():
        return
    running = _state["running"]
    if running == 'ROTATE' and _state["angle"] is not None:
        i, deg, center = _state["angle"]
        text = "{:s}  {:+.1f}°".format("XYZ"[i], deg)
    elif running == 'SCALE' and _state["scale"] is not None:
        factors, center = _state["scale"]
        text = "   ".join("{:s} ×{:.3f}".format("XYZ"[i], f) for i, f in enumerate(factors))
    else:
        return
    context = bpy.context
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return
    p2d = view3d_utils.location_3d_to_region_2d(region, rv3d, center)
    if p2d is None:
        return
    font = 0
    size = int(14 * context.preferences.system.ui_scale)
    blf.size(font, size)
    x =p2d.x + _prefs().axis_overlay_size * 0.75
    y = p2d.y + _prefs().axis_overlay_size * 0.75
    blf.color(font, 0.0, 0.0, 0.0, 0.7)
    blf.position(font, x + 1, y - 1, 0)
    blf.draw(font, text)
    blf.color(font, *_HIGHLIGHT_COLOR)
    blf.position(font, x, y, 0)
    blf.draw(font, text)


# -----------------------------------------------------------------------------
# 애드온 자체 변환 (기즈모로 잡았을 때)
#
# Blender 변환(transform.*)은 축 고정선(끝없이 뻗는 축 선)을 항상 그리고 끌 방법이 없다.
# Maya/Max 모드에서는 기즈모를 잡으면 이 연산자가 직접 이동/회전/스케일을 계산해서 그 선이 나오지 않는다.
# G/R/S 키 변환은 그대로 Blender 변환을 쓴다.

def _own_transform_on():
    return _current_mode() in {'MAYA', 'MAX'}


def _closest_axis_param(center, a, origin, direction):
    """마우스 광선(origin + s*direction)에 가장 가까운 축 위 점의 파라미터 t (center + t*a). 평행하면 None."""
    w0 = center - origin
    b = a.dot(direction)
    c = direction.dot(direction)
    d = a.dot(w0)
    e = direction.dot(w0)
    denom = c - b * b
    if abs(denom) < 1e-8:
        return None
    return (b * e - c * d) / denom


def _ray_plane_hit(origin, direction, point, normal):
    denom = direction.dot(normal)
    if abs(denom) < 1e-8:
        return None
    s = (point - origin).dot(normal) / denom
    return origin + direction * s


def _rot_mode_path(rotation_mode):
    if rotation_mode == 'QUATERNION':
        return "rotation_quaternion"
    if rotation_mode == 'AXIS_ANGLE':
        return "rotation_axis_angle"
    return "rotation_euler"


def _apply_basis(owner, basis, start, prev_rot):
    """
    분해한 basis 행렬을 loc/rot/scale 채널에 넣는다. 잠긴 채널은 시작값을 유지하고,
    오일러는 직전 값과 연속이 되게(180° 넘어도 튀지 않게) 넣는다. 넣은 회전값을 돌려준다.
    """
    loc, quat, scale = basis.decompose()
    start_loc, start_rot, start_scale = start
    owner.location = [start_loc[i] if owner.lock_location[i] else loc[i] for i in range(3)]
    owner.scale = [start_scale[i] if owner.lock_scale[i] else scale[i] for i in range(3)]
    mode = owner.rotation_mode
    if mode == 'QUATERNION':
        if quat.dot(prev_rot) < 0.0:
            quat.negate()
        owner.rotation_quaternion = quat
        return quat.copy()
    if mode == 'AXIS_ANGLE':
        axis, angle = quat.to_axis_angle()
        owner.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
        return quat.copy()
    eul = quat.to_euler(mode, prev_rot)
    owner.rotation_euler = [start_rot[i] if owner.lock_rotation[i] else eul[i] for i in range(3)]
    return owner.rotation_euler.copy()


def _rot_value(owner):
    mode = owner.rotation_mode
    if mode == 'QUATERNION':
        return owner.rotation_quaternion.copy()
    if mode == 'AXIS_ANGLE':
        angle, x, y, z = owner.rotation_axis_angle
        return Quaternion((x, y, z), angle)
    return owner.rotation_euler.copy()


class DCCNAV_OT_gizmo_transform(bpy.types.Operator):
    """DCC 기즈모로 잡은 축을 따라 이동/회전/스케일 (축 고정선 없이)"""
    bl_idname = "dccnav.gizmo_transform"
    bl_label = "DCC 기즈모 변환"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    kind: bpy.props.EnumProperty(
        items=(('MOVE', "이동", ""), ('ROTATE', "회전", ""), ('SCALE', "스케일", "")),
        options={'SKIP_SAVE'},
    )
    axis: bpy.props.IntProperty(min=0, max=2, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.mode in _GIZMO_MODES and context.region_data is not None

    # --- 대상 수집 ---------------------------------------------------------

    def _collect(self, context):
        mode = context.mode
        self.objects = []
        self.bones = []
        self.verts = None
        self.ebones = None
        if mode == 'OBJECT':
            sel = set(context.selected_objects)

            def has_selected_parent(o):
                p = o.parent
                while p is not None:
                    if p in sel:
                        return True
                    p = p.parent
                return False

            for o in context.selected_objects:
                if not has_selected_parent(o):
                    self.objects.append({
                        "obj": o,
                        "world": o.matrix_world.copy(),
                        "start": (o.location.copy(), _rot_value(o), o.scale.copy()),
                        "prev": _rot_value(o),
                    })
        elif mode == 'POSE':
            sel = set(context.selected_pose_bones or [])

            def has_selected_parent(pb):
                p = pb.parent
                while p is not None:
                    if p in sel:
                        return True
                    p = p.parent
                return False

            for pb in sel:
                if not has_selected_parent(pb):
                    arm = pb.id_data
                    self.bones.append({
                        "pb": pb,
                        "arm": arm,
                        "world": arm.matrix_world @ pb.matrix,
                        "start": (pb.location.copy(), _rot_value(pb), pb.scale.copy()),
                        "prev": _rot_value(pb),
                    })
        elif mode == 'EDIT_MESH':
            obj = context.active_object
            bm = bmesh.from_edit_mesh(obj.data)
            mw = obj.matrix_world
            self.verts = {
                "obj": obj, "bm": bm, "mw": mw.copy(), "imw": mw.inverted(),
                "items": [(v, mw @ v.co) for v in bm.verts if v.select],
            }
        elif mode == 'EDIT_ARMATURE':
            obj = context.active_object
            mw = obj.matrix_world
            items = []
            for eb in obj.data.edit_bones:
                if eb.select_head or eb.select_tail:
                    items.append((eb, eb.select_head, eb.select_tail, mw @ eb.head, mw @ eb.tail, eb.roll))
            self.ebones = {"obj": obj, "mw": mw.copy(), "imw": mw.inverted(), "items": items}

    def _has_targets(self):
        return bool(self.objects or self.bones or (self.verts and self.verts["items"])
                    or (self.ebones and self.ebones["items"]))

    # --- 적용 ---------------------------------------------------------------

    def _apply(self, context, delta):
        for item in self.objects:
            o = item["obj"]
            o.matrix_world = delta @ item["world"]
            item["prev"] = _apply_basis(o, o.matrix_basis.copy(), item["start"], item["prev"])
        for item in self.bones:
            pb = item["pb"]
            arm = item["arm"]
            pose_m = arm.matrix_world.inverted() @ delta @ item["world"]
            basis = arm.convert_space(pose_bone=pb, matrix=pose_m, from_space='POSE', to_space='LOCAL')
            item["prev"] = _apply_basis(pb, basis, item["start"], item["prev"])
        if self.verts is not None:
            m = self.verts["imw"] @ delta
            for v, co in self.verts["items"]:
                v.co = m @ co
            bmesh.update_edit_mesh(self.verts["obj"].data, loop_triangles=False, destructive=False)
        if self.ebones is not None:
            imw = self.ebones["imw"]
            for eb, sh, st, head, tail, roll in self.ebones["items"]:
                if sh:
                    eb.head = imw @ (delta @ head)
                else:
                    eb.head = imw @ head
                if st:
                    eb.tail = imw @ (delta @ tail)
                else:
                    eb.tail = imw @ tail
                eb.roll = roll
        context.view_layer.update()

    def _restore(self, context):
        self._apply(context, Matrix.Identity(4))
        for item in self.objects:
            o = item["obj"]
            loc, rot, scale = item["start"]
            o.location, o.scale = loc, scale
            self._set_rot(o, rot)
        for item in self.bones:
            pb = item["pb"]
            loc, rot, scale = item["start"]
            pb.location, pb.scale = loc, scale
            self._set_rot(pb, rot)
        context.view_layer.update()

    @staticmethod
    def _set_rot(owner, rot):
        mode = owner.rotation_mode
        if mode == 'QUATERNION':
            owner.rotation_quaternion = rot
        elif mode == 'AXIS_ANGLE':
            axis, angle = rot.to_axis_angle()
            owner.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
        else:
            owner.rotation_euler = rot

    def _autokey(self, context):
        if not context.scene.tool_settings.use_keyframe_insert_auto:
            return
        frame = context.scene.frame_current
        if self.kind == 'MOVE':
            paths = lambda owner: ["location"]
        elif self.kind == 'ROTATE':
            paths = lambda owner: [_rot_mode_path(owner.rotation_mode)]
        else:
            paths = lambda owner: ["scale"]
        for item in self.objects:
            for p in paths(item["obj"]):
                item["obj"].keyframe_insert(p, frame=frame)
        for item in self.bones:
            pb = item["pb"]
            for p in paths(pb):
                pb.keyframe_insert(p, frame=frame, group=pb.name)

    # --- 마우스 → 변환 -----------------------------------------------------

    def _ray(self, context, mouse):
        region = context.region
        rv3d = context.region_data
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse)
        return origin, direction

    def _value(self, context, event):
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        precise = event.shift
        snap = event.ctrl
        if self.kind == 'MOVE':
            o, d = self._ray(context, mouse)
            t = _closest_axis_param(self.center, self.a, o, d)
            if t is None:
                return self.value
            value = t - self.t0
            if precise:
                value *= 0.1
            if snap:
                step = context.scene.unit_settings.scale_length or 1.0
                value = round(value / (0.1 * step)) * (0.1 * step)
            return value
        if self.kind == 'ROTATE':
            v = mouse - self.c2d
            if v.length < 1.0 or self.last_vec.length < 1.0:
                return self.value
            ang = math.atan2(self.last_vec.x * v.y - self.last_vec.y * v.x, self.last_vec.dot(v))
            self.last_vec = v
            self.raw += ang * (0.1 if precise else 1.0)
            value = self.raw * self.view_sign
            if snap:
                step = math.radians(5.0)
                value = round(value / step) * step
            return value
        # SCALE
        proj = (mouse - self.c2d).dot(self.s2d)
        if abs(self.d0) < 1e-6:
            return self.value
        factor = proj / self.d0
        if precise:
            factor = 1.0 + (factor - 1.0) * 0.1
        if snap:
            factor = round(factor / 0.1) * 0.1
        return factor

    def _delta(self):
        c, a = self.center, self.a
        if self.kind == 'MOVE':
            return Matrix.Translation(a * self.value)
        if self.kind == 'ROTATE':
            return Matrix.Translation(c) @ Matrix.Rotation(self.value, 4, a) @ Matrix.Translation(-c)
        f = self.value
        s3 = Matrix.Identity(3)
        for r in range(3):
            for col in range(3):
                s3[r][col] += (f - 1.0) * a[r] * a[col]
        return Matrix.Translation(c) @ s3.to_4x4() @ Matrix.Translation(-c)

    def _publish(self):
        factors = [1.0, 1.0, 1.0]
        if self.kind == 'SCALE':
            factors[self.axis] = self.value
        _state["op"] = {
            "kind": self.kind,
            "axis": self.axis,
            "axis_vec": self.a.copy(),
            "value": self.value,
            "r0": self.r0,
            "factors": tuple(factors),
        }

    def _header(self, context):
        name = "XYZ"[self.axis]
        if self.kind == 'MOVE':
            text = "이동 {:s}: {:.4f}".format(name, self.value)
        elif self.kind == 'ROTATE':
            text = "회전 {:s}: {:+.2f}°".format(name, math.degrees(self.value))
        else:
            text = "스케일 {:s}: ×{:.3f}".format(name, self.value)
        text += "    Ctrl: 스냅   Shift: 정밀   우클릭/Esc: 취소"
        context.area.header_text_set(text)

    # --- 연산자 ---------------------------------------------------------------

    def invoke(self, context, event):
        self._collect(context)
        if not self._has_targets():
            return {'CANCELLED'}
        center = _selection_center(context)
        if center is None:
            return {'CANCELLED'}
        rot = _orientation_matrix(context)
        self.center = center
        self.a = rot.col[self.axis].normalized()
        region = context.region
        rv3d = context.region_data
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        self.c2d = view3d_utils.location_3d_to_region_2d(region, rv3d, center) or mouse.copy()
        self.r0 = None

        if self.kind == 'MOVE':
            o, d = self._ray(context, mouse)
            t0 = _closest_axis_param(center, self.a, o, d)
            if t0 is None:
                return {'CANCELLED'}
            self.t0 = t0
            self.value = 0.0
        elif self.kind == 'ROTATE':
            self.last_vec = mouse - self.c2d
            self.raw = 0.0
            toward = rv3d.view_rotation @ Vector((0.0, 0.0, 1.0))
            # 화면에서 반시계로 돌리면, 축이 보는 사람 쪽을 향할 때 + 회전이다.
            self.view_sign = 1.0 if self.a.dot(toward) >= 0.0 else -1.0
            o, d = self._ray(context, mouse)
            hit = _ray_plane_hit(o, d, center, self.a)
            if hit is not None and (hit - center).length > 1e-6:
                self.r0 = (hit - center).normalized()
            self.value = 0.0
        else:
            tip2d = view3d_utils.location_3d_to_region_2d(region, rv3d, center + self.a)
            if tip2d is None or (tip2d - self.c2d).length < 1e-3:
                return {'CANCELLED'}
            self.s2d = (tip2d - self.c2d).normalized()
            self.d0 = (mouse - self.c2d).dot(self.s2d)
            if abs(self.d0) < 4.0:
                self.d0 = 4.0 if self.d0 >= 0.0 else -4.0
            self.value = 1.0

        _state["drag"] = self.axis
        self._publish()
        self._header(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_SHIFT', 'RIGHT_SHIFT'}:
            self.value = self._value(context, event)
            self._apply(context, self._delta())
            self._publish()
            self._header(context)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._finish(context)
            self._autokey(context)
            return {'FINISHED'}
        if event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self._restore(context)
            self._finish(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        _state["op"] = None
        context.area.header_text_set(None)
        context.area.tag_redraw()


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

    def _bind(self, orient, own):
        for kind, items in self.handles.items():
            for i, gz in enumerate(items):
                if own:
                    # Maya/Max 모드: 애드온이 직접 변환해서 Blender 축 고정선이 나오지 않는다.
                    props = gz.target_set_operator("dccnav.gizmo_transform")
                    props.kind = kind
                    props.axis = i
                    continue
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
        self.bound = (orient, own)

    def draw_prepare(self, context):
        kind = _active_tool_kind(context)
        frame = _gizmo_frame(context) if kind is not None else None
        orient = context.scene.transform_orientation_slots[0].type
        own = _own_transform_on()
        if self.bound != (orient, own):
            self._bind(orient, own)

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
    DCCNAV_OT_gizmo_transform,
    VIEW3D_GGT_dcc_navigation,
)


# -----------------------------------------------------------------------------
# 등록

def register():
    global _draw_handle, _text_handle
    for cls in _classes:
        bpy.utils.register_class(cls)
    mode = _current_mode()
    _register_keymaps(mode, _hotkeys_on())
    if bpy.context.window_manager.keyconfigs.user is not None:
        _disable_conflicts()
    bpy.app.timers.register(_startup_apply, first_interval=1.0)
    if not bpy.app.background:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_gizmo, (), 'WINDOW', 'POST_VIEW',
        )
        _text_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_angle_text, (), 'WINDOW', 'POST_PIXEL',
        )
        bpy.app.timers.register(_tool_gizmo_timer, first_interval=0.5, persistent=True)


def unregister():
    global _draw_handle, _text_handle
    if bpy.app.timers.is_registered(_tool_gizmo_timer):
        bpy.app.timers.unregister(_tool_gizmo_timer)
    _restore_tool_gizmos()
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if _text_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_text_handle, 'WINDOW')
        _text_handle = None
    if bpy.app.timers.is_registered(_startup_apply):
        bpy.app.timers.unregister(_startup_apply)
    _restore_conflicts()
    _unregister_keymaps()
    # 활성 키맵에 남은 애드온 항목 사본을 바로 걷어낸다.
    if bpy.context.window_manager.keyconfigs.user is not None:
        bpy.context.window_manager.keyconfigs.update()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
