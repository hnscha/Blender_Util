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
    # Maya / Unreal / Unity 공통 (Maya 기준): 뷰 조작은 모두 Alt + 마우스.
    # 가운데 버튼만 끌기 = Maya 처럼 마지막으로 클릭한 기즈모 핸들(활성 핸들)을 화면 어디서나 조작.
    'MAYA': (
        ("view3d.rotate", "LEFTMOUSE", True, False),
        ("view3d.move", "MIDDLEMOUSE", True, False),
        ("view3d.zoom", "RIGHTMOUSE", True, False),
        ("dccnav.mmb_manipulate", "MIDDLEMOUSE", False, False),
        # Alt 를 누른 채 휠을 굴려도 줌 (Alt 로 뷰를 다루다가 휠을 굴리는 경우).
        ("view3d.zoom", "WHEELINMOUSE", True, False, {"delta": 1}),
        ("view3d.zoom", "WHEELOUTMOUSE", True, False, {"delta": -1}),
    ),
    'MAX': (
        ("view3d.move", "MIDDLEMOUSE", False, False),
        ("view3d.rotate", "MIDDLEMOUSE", True, False),
        ("view3d.zoom", "MIDDLEMOUSE", True, True),
        ("view3d.zoom", "WHEELINMOUSE", True, False, {"delta": 1}),
        ("view3d.zoom", "WHEELOUTMOUSE", True, False, {"delta": -1}),
    ),
}

_MODE_ITEMS = (
    ('BLENDER', "Blender Default", "Blender's default viewport navigation"),
    ('MAYA', "Maya / Unreal / Unity", "Alt + Left/Middle/Right drag = Orbit/Pan/Zoom"),
    ('MAX', "3ds Max", "Middle drag = Pan, Alt+Middle = Orbit, Ctrl+Alt+Middle = Zoom"),
)

_MODE_HELP = {
    'BLENDER': (
        "MMB : Orbit",
        "Shift + MMB : Pan",
        "Ctrl + MMB / Wheel : Zoom",
    ),
    'MAYA': (
        "Alt + LMB : Orbit",
        "Alt + MMB : Pan",
        "Alt + RMB / Wheel / Alt + Wheel : Zoom",
        "MMB : Drag active gizmo handle",
    ),
    'MAX': (
        "MMB : Pan",
        "Alt + MMB : Orbit",
        "Ctrl + Alt + MMB / Wheel / Alt + Wheel : Zoom",
    ),
}

_HOTKEY_HELP = (
    "Q W E R : Select Move Rotate Scale",
    "1 / 2 : World / Local",
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
# 기즈모 드래그 시작 판정 디버그용.
_debug_invoke = {}


# -----------------------------------------------------------------------------
# 키 조합

_HOTKEY_COMBOS = {(key, False, False) for key, _ in _TOOL_KEYS} | {(key, False, False) for key, _ in _ORIENT_KEYS}

# 애드온 키는 없지만 그 모드에서 꺼 둘 Blender 기본 키 (키, alt, ctrl).
# Maya/Max 사용자는 Alt 를 누른 채 뷰를 다루다 휠을 굴리는 일이 잦아서, Alt + 휠 = 프레임 이동을 끈다.
_MODE_EXTRA_OFF = {
    'BLENDER': set(),
    'MAYA': {("WHEELUPMOUSE", True, False), ("WHEELDOWNMOUSE", True, False)},
    'MAX': {("WHEELUPMOUSE", True, False), ("WHEELDOWNMOUSE", True, False)},
}


def _mode_combos(mode, hotkeys):
    """지금 쓰는 (키, alt, ctrl) 조합. shift/oskey 는 쓰지 않는다."""
    combos = {(item[1], item[2], item[3]) for item in _MODES[mode]} | _MODE_EXTRA_OFF[mode]
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
        for item in nav:
            idname, mouse, alt, ctrl = item[:4]
            props = item[4] if len(item) > 4 else {}
            add(km, idname, mouse, alt=alt, ctrl=ctrl, props=props)
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
        for item in nav:
            idname, mouse, alt, ctrl = item[:4]
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


def _apply_zoom_direction(mode):
    """
    드래그 줌 방향. Blender 기본은 Maya/Max 와 반대라서 Maya/Max 모드에서는 Invert Zoom Direction 을 켠다.
    원래 사용자 값은 저장해 두었다가 Blender 기본 모드로 돌아가거나 애드온을 끄면 되돌린다.
    (휠 줌 방향은 따로 설정이라 건드리지 않는다.)
    """
    prefs = _prefs()
    if prefs is None:
        return
    inputs = bpy.context.preferences.inputs
    if mode in {'MAYA', 'MAX'}:
        if prefs.saved_invert_zoom < 0:
            prefs.saved_invert_zoom = int(inputs.invert_mouse_zoom)
        inputs.invert_mouse_zoom = True
    else:
        _restore_zoom_direction()


def _restore_zoom_direction():
    prefs = _prefs()
    if prefs is None or prefs.saved_invert_zoom < 0:
        return
    bpy.context.preferences.inputs.invert_mouse_zoom = bool(prefs.saved_invert_zoom)
    prefs.saved_invert_zoom = -1


def _apply_mode(mode, hotkeys):
    _restore_conflicts()
    _unregister_keymaps()
    _register_keymaps(mode, hotkeys)
    _sync_user_keymaps()
    _disable_conflicts()
    _apply_zoom_direction(mode)


def _startup_apply():
    # 시작 직후에는 활성 키맵이 아직 다 만들어지지 않았을 수 있어 한 번 더 맞춘다.
    _sync_user_keymaps()
    _disable_conflicts()
    _apply_zoom_direction(_current_mode())
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


def _redraw_view3d():
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _update_gizmo_size(_self, _context):
    _redraw_view3d()


class DCCNAV_OT_gizmo_size(bpy.types.Operator):
    """Make the DCC gizmo smaller or bigger"""
    bl_idname = "dccnav.gizmo_size"
    bl_label = "DCC Gizmo Size"
    bl_options = {'INTERNAL'}

    delta: bpy.props.IntProperty(default=10, options={'SKIP_SAVE'})

    def execute(self, _context):
        prefs = _prefs()
        if prefs is None:
            return {'CANCELLED'}
        prefs.axis_overlay_size = max(20, min(400, prefs.axis_overlay_size + self.delta))
        return {'FINISHED'}


_MODE_LABELS = {ident: label for ident, label, _desc in _MODE_ITEMS}
_WORK_LABELS = {'ANIMATION': "Animation", 'RIGGING': "Rigging"}


def _pending_differs(prefs):
    return prefs.pending_nav_mode != prefs.nav_mode or prefs.pending_work_mode != prefs.work_mode


def _draw_mode_ui(layout, prefs):
    # UI 는 영어 고정: Blender 인터페이스 번역(예: 한국어)을 적용하지 않는다 (translate=False).
    # 모드 버튼은 '고를 것(pending)'만 바꾸고, Apply 를 눌러야 실제로 전환된다 (전환 직후 엉뚱한 조작 방지).
    box = layout.box()
    box.label(
        text="Active: {:s} / {:s}".format(_MODE_LABELS[prefs.nav_mode], _WORK_LABELS[prefs.work_mode]),
        icon='CHECKMARK', translate=False,
    )

    layout.label(text="Viewport Navigation", translate=False)
    col = layout.column(align=True)
    # 열거형 버튼 이름은 expand 로 그리면 Blender 가 번역해 버려서, 버튼을 하나씩 영어 이름으로 그린다.
    for ident, label, _desc in _MODE_ITEMS:
        col.prop_enum(prefs, "pending_nav_mode", ident, text=label, translate=False)
    box = layout.box()
    col = box.column(align=True)
    for line in _MODE_HELP[prefs.pending_nav_mode]:
        col.label(text=line, translate=False)

    layout.label(text="Work Mode", translate=False)
    row = layout.row(align=True)
    row.prop_enum(prefs, "pending_work_mode", 'ANIMATION', text="Animation", translate=False)
    row.prop_enum(prefs, "pending_work_mode", 'RIGGING', text="Rigging", translate=False)
    sub = layout.column(align=True)
    if prefs.pending_work_mode == 'ANIMATION':
        sub.label(text="Object/Pose/Edit (mesh, armature): custom gizmo", translate=False)
        sub.label(text="G/R/S keys: Blender transform", translate=False)
    else:
        sub.label(text="Blender gizmo everywhere", translate=False)
        sub.label(text="Snap, numeric input, Auto IK", translate=False)

    row = layout.row()
    row.scale_y = 1.4
    differs = _pending_differs(prefs)
    row.alert = differs
    row.operator(
        "dccnav.apply_mode",
        text="Apply" if differs else "Applied",
        icon='FILE_REFRESH' if differs else 'CHECKMARK',
        translate=False,
    )

    layout.separator()
    layout.prop(prefs, "use_hotkeys", translate=False)
    box = layout.box()
    box.active = prefs.use_hotkeys
    col = box.column(align=True)
    for line in _HOTKEY_HELP:
        col.label(text=line, translate=False)

    layout.separator()
    layout.prop(prefs, "show_axis_overlay", translate=False)
    col = layout.column()
    col.active = prefs.show_axis_overlay
    # 기즈모 크기: 슬라이더 + 한 단계씩 줄이기/키우기 버튼. 바꾸면 바로 보이고 설정에 저장된다.
    row = col.row(align=True)
    row.operator("dccnav.gizmo_size", text="", icon='REMOVE', translate=False).delta = -10
    row.prop(prefs, "axis_overlay_size", text="Gizmo Size", slider=True, translate=False)
    row.operator("dccnav.gizmo_size", text="", icon='ADD', translate=False).delta = 10
    col.prop(prefs, "scale_sensitivity", slider=True, translate=False)

    # 피봇 고르기 (Blender 피봇 설정을 그대로 바꾼다. 누르면 바로 적용).
    ts = bpy.context.scene.tool_settings if bpy.context.scene else None
    if ts is not None:
        layout.separator()
        layout.label(text="Pivot", translate=False)
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop_enum(ts, "transform_pivot_point", 'MEDIAN_POINT', text="Shared Center", translate=False)
        row.prop_enum(ts, "transform_pivot_point", 'INDIVIDUAL_ORIGINS', text="Individual", translate=False)
        row = col.row(align=True)
        row.prop_enum(ts, "transform_pivot_point", 'ACTIVE_ELEMENT', text="Active", translate=False)
        row.prop_enum(ts, "transform_pivot_point", 'BOUNDING_BOX_CENTER', text="Bounds", translate=False)
        row.prop_enum(ts, "transform_pivot_point", 'CURSOR', text="3D Cursor", translate=False)
        if ts.transform_pivot_point == 'INDIVIDUAL_ORIGINS':
            col.label(text="Each bone/object turns in place", translate=False)
        else:
            col.label(text="All turn around one center", translate=False)
        col.prop(prefs, "default_individual_pivot", translate=False)


class DCCNAV_OT_apply_mode(bpy.types.Operator):
    """Apply the selected Viewport Navigation and Work Mode"""
    bl_idname = "dccnav.apply_mode"
    bl_label = "Apply Navigation Mode"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        prefs = _prefs()
        if prefs is None:
            return {'CANCELLED'}
        nav, work = prefs.pending_nav_mode, prefs.pending_work_mode
        prefs.work_mode = work
        if prefs.nav_mode != nav:
            prefs.nav_mode = nav            # update 콜백이 키맵을 다시 만든다.
        else:
            _apply_mode(nav, prefs.use_hotkeys)
        # 활성 키맵을 지금 바로 다시 합쳐서, 전환 직후에도 새 조작만 동작하게 한다.
        _sync_user_keymaps()
        _disable_conflicts()
        context.window_manager.keyconfigs.update()
        _sync_tool_gizmos()
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                area.tag_redraw()
        self.report({'INFO'}, "DCC Navigation: switched to {:s} / {:s}".format(
            _MODE_LABELS[nav], _WORK_LABELS[work]))
        return {'FINISHED'}


class DCCNavigationPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    nav_mode: bpy.props.EnumProperty(
        name="Navigation Mode",
        description="Viewport mouse navigation style (hotkeys are toggled separately)",
        items=_MODE_ITEMS,
        default='MAYA',
        update=_update_keys,
    )
    # 패널에서 고른 값 (Apply 전까지 적용 안 됨).
    pending_nav_mode: bpy.props.EnumProperty(
        name="Navigation Mode (selected)",
        items=_MODE_ITEMS,
        default='MAYA',
    )
    pending_work_mode: bpy.props.EnumProperty(
        name="Work Mode (selected)",
        items=(
            ('ANIMATION', "Animation", "Object/Pose/Edit mode: custom gizmo without constraint lines"),
            ('RIGGING', "Rigging", "Blender's gizmo and transform everywhere"),
        ),
        default='ANIMATION',
    )
    use_hotkeys: bpy.props.BoolProperty(
        name="Hotkeys (QWER, 1/2)",
        description="Q/W/E/R tool switching and 1/2 World/Local orientation, independent of the navigation mode",
        default=True,
        update=_update_keys,
    )
    show_axis_overlay: bpy.props.BoolProperty(
        name="DCC Gizmo",
        description=(
            "Replace Blender's Move/Rotate/Scale tool gizmo with one that looks the same while idle and while transforming.\n"
            "All X/Y/Z handles stay visible during the transform"
        ),
        default=True,
        update=_update_gizmo_pref,
    )
    axis_overlay_size: bpy.props.IntProperty(
        name="Gizmo Size (px)",
        description="Size of the DCC gizmo on screen, in pixels",
        default=80,
        min=20,
        max=400,
        update=_update_gizmo_size,
    )
    work_mode: bpy.props.EnumProperty(
        name="Work Mode",
        description="How gizmo drags are computed",
        items=(
            ('ANIMATION', "Animation",
             "Object/Pose/Edit (mesh, armature) mode: the add-on computes gizmo drags, no constraint lines (Maya/Max mode only)"),
            ('RIGGING', "Rigging",
             "Blender's transform everywhere: snapping, numeric input, Auto IK, mirror (constraint lines shown)"),
        ),
        default='ANIMATION',
    )
    # Maya/Max 모드에 들어가기 전 Invert Zoom Direction 값 (-1 = 저장 안 됨). 되돌릴 때 쓴다.
    saved_invert_zoom: bpy.props.IntProperty(default=-1, options={'HIDDEN'})
    default_individual_pivot: bpy.props.BoolProperty(
        name="Default Pivot: Individual",
        description="Set the pivot to Individual Origins whenever a file is opened or created",
        default=True,
    )
    scale_sensitivity: bpy.props.FloatProperty(
        name="Scale Sensitivity",
        description="Gizmo scale sensitivity. At 1.0, dragging one gizmo length adds 1x (default 0.5, lower is less sensitive)",
        default=0.5,
        min=0.05,
        max=3.0,
        step=5,
        precision=2,
    )

    def draw(self, context):
        layout = self.layout
        inputs = context.preferences.inputs

        _draw_mode_ui(layout, self)

        if self.nav_mode == 'MAYA' and inputs.use_mouse_emulate_3_button:
            box = layout.box()
            box.alert = True
            box.label(text="Emulate 3 Button Mouse is on and conflicts with Alt+Left drag.", icon='ERROR', translate=False)
            box.prop(inputs, "use_mouse_emulate_3_button", translate=False)

        box = layout.box()
        box.label(text="Blender Navigation Options (optional)", translate=False)
        box.prop(inputs, "use_zoom_to_mouse", translate=False)
        box.prop(inputs, "use_rotate_around_active", translate=False)

        disabled = _conflict_items(_mode_combos(self.nav_mode, self.use_hotkeys), active_only=False)
        if disabled:
            box = layout.box()
            box.label(text="Disabled conflicting default shortcuts ({:d})".format(len(disabled)), translate=False)
            col = box.column(align=True)
            for km, kmi in disabled:
                col.label(text="{:s} : {:s}  ({:s})".format(km.name, _key_text(kmi), kmi.name or kmi.idname), translate=False)


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

# 축(0~2) 말고 잡을 수 있는 핸들 번호.
H_CENTER = 3       # 이동: 가운데 사각형(화면 평면 자유 이동), 스케일: 가운데(전체 스케일)
H_VIEW_RING = 3    # 회전: 흰 바깥 링(화면 축 회전)
H_TRACKBALL = 4    # 회전: 링 안쪽(자유 회전)
H_PLANE = 4        # 이동: 평면 핸들 4/5/6 = 법선 X/Y/Z (YZ/XZ/XY 평면)

# 핸들 크기 (기즈모 길이 대비).
_CENTER_SIZE = 0.12
_PLANE_OFFSET = 0.32
_PLANE_SIZE = 0.09
_VIEW_RING_RADIUS = 1.2
_TRACKBALL_RADIUS = 0.95
_PIVOT_AXIS_SIZE = 0.3     # 회전 기즈모 가운데 작은 축 표시 길이
_MOVE_PLANE_SIZE = 0.35    # 이동 평면 꺾쇠 크기 (Max)
_SCALE_INNER = 0.4         # 스케일 가운데 삼각형 크기 (Max)
_SCALE_OUTER = 0.62        # 스케일 사다리꼴 바깥 변 (Max)

# 마우스가 올라간 축 (GizmoGroup 이 갱신), 변환을 시작할 때 잡은 축.
_state = {
    "hover": None, "drag": None, "running": None,
    "start_q": None, "start_m3": None, "start_rot": None,
    "angle": None, "scale": None,
    # 애드온 자체 변환(DCCNAV_OT_gizmo_transform) 진행 상황. 그 밖에는 None.
    "op": None,
    # Maya 활성 핸들: 종류별로 마지막으로 잡은 부분. 처음엔 가운데(이동 화면 평면 / 자유 회전 / 전체 스케일).
    "active": {'MOVE': 3, 'ROTATE': 4, 'SCALE': 3},
}
_text_handle = None
# 우리가 Blender 툴 기즈모를 숨긴 SpaceView3D 들 (as_pointer).
_hidden_spaces = set()


def _gizmo_enabled():
    prefs = _prefs()
    return prefs is not None and prefs.show_axis_overlay


def _custom_gizmo_here(context):
    """
    여기서 DCC 기즈모를 쓸지. 애니메이션 작업(Work Mode = Animation)의 오브젝트·포즈·에디트(메쉬, 아마추어) 모드.
    리깅(Work Mode = Rigging)과 그 밖의 에디트 모드(커브·격자 등)는 Blender 기본 기즈모·변환을 그대로 쓴다.
    """
    prefs = _prefs()
    return (prefs is not None and prefs.show_axis_overlay and prefs.work_mode == 'ANIMATION'
            and context.mode in _GIZMO_MODES)


def _active_tool_kind(context):
    if not _custom_gizmo_here(context):
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
                names = []
                for o in ops:
                    part = getattr(o.properties, "axis", None) if o.bl_idname == "DCCNAV_OT_gizmo_transform" else None
                    names.append(o.bl_idname if part is None else "{:s}[{}]".format(o.bl_idname, part))
                _last_seen.append((time.time(), names, kind))
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

    if mode == 'EDIT_MESH':
        # 여러 오브젝트를 같이 편집할 때도 선택된 점을 모두 모은다.
        for o in context.objects_in_mode_unique_data:
            bm = bmesh.from_edit_mesh(o.data)
            mw = o.matrix_world
            pts += [mw @ v.co for v in bm.verts if v.select]
            act = bm.select_history.active
            if o == obj and isinstance(act, bmesh.types.BMVert):
                active = mw @ act.co
    elif mode == 'EDIT_ARMATURE':
        for o in context.objects_in_mode_unique_data:
            mw = o.matrix_world
            for b in o.data.edit_bones:
                if b.select_head:
                    pts.append(mw @ b.head)
                if b.select_tail:
                    pts.append(mw @ b.tail)
            if o == obj and o.data.edit_bones.active is not None:
                active = mw @ o.data.edit_bones.active.head
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


def _ring_front(offset, toward, length):
    """링 위의 점(중심 기준 offset)이 보는 사람 쪽 반원인지. 화면과 나란한 링은 전부 앞쪽."""
    return offset.dot(toward) >= -0.02 * length


def _axis_facing_view(axis, toward):
    """축이 화면을 거의 똑바로 향해서 점처럼 보이는지 (Max/Maya 는 이런 축 핸들을 숨긴다)."""
    return abs(axis.normalized().dot(toward)) > 0.97


def _plane_edge_on(normal, toward):
    """두 축 평면이 화면에서 옆으로 누워 선처럼 보이는지 (이때 평면 핸들을 숨긴다)."""
    return abs(normal.normalized().dot(toward)) < 0.12


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


def _move_plane_quad(center, rot, length, n):
    """이동 평면 핸들(법선 n 축)의 꺾쇠 세 점 (u 끝, 모서리, v 끝)과 잡는 영역 사각형."""
    u = rot.col[(n + 1) % 3].normalized()
    v = rot.col[(n + 2) % 3].normalized()
    s = length * _MOVE_PLANE_SIZE
    pu, pc, pv = center + u * s, center + (u + v) * s, center + v * s
    return pu, pc, pv, [center, pu, pc, pv]


def _scale_center_tri(center, rot, length):
    """스케일 가운데 삼각형 (세 축 위의 점을 잇는다) = 전체 스케일."""
    a = length * _SCALE_INNER
    return [center + rot.col[i].normalized() * a for i in range(3)]


def _scale_plane_quad(center, rot, length, n):
    """스케일 평면 핸들(법선 n 축) 사다리꼴: 안쪽 변(가운데 삼각형 쪽)과 바깥 변."""
    u = rot.col[(n + 1) % 3].normalized()
    v = rot.col[(n + 2) % 3].normalized()
    a, b = length * _SCALE_INNER, length * _SCALE_OUTER
    return [center + u * a, center + u * b, center + v * b, center + v * a]


def _draw_gizmo():
    context = bpy.context
    _state["labels"] = []
    if not _gizmo_enabled() or not _custom_gizmo_here(context):
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
    if hot is None and _current_mode() == 'MAYA':
        # Maya 처럼 활성 핸들(가운데 버튼으로 조작할 부분)을 노랗게 표시한다.
        hot = _state["active"].get(kind)

    # Maya/Max 처럼 스케일 중에는 핸들이 배율만큼 늘었다 줄었다 하고, 바뀌는 축이 강조된다.
    own = _state["op"]
    if _state["running"] != 'SCALE':
        factors = None
    elif own is not None and own["kind"] == 'SCALE':
        factors = own["factors"]
    else:
        factors = _scale_factors(context)
    _state["scale"] = (factors, center.copy()) if factors is not None else None

    toward = rv3d.view_rotation @ Vector((0.0, 0.0, 1.0))

    # 3ds Max 기즈모 모양: 이동 = 화살표 + 모서리 꺾쇠 평면, 회전 = 축별 링, 스케일 = 축 + 사다리꼴·가운데 삼각형.
    labels = []
    for i in range(3):
        scaling = factors is not None and abs(factors[i] - 1.0) > 1e-3
        color = _HIGHLIGHT_COLOR if hot == i or scaling else _AXIS_COLORS[i]
        width = 3.5 if hot == i or scaling else 2.5
        side, up, axis = _axis_basis(rot, i)

        if kind == 'ROTATE':
            # Max/Maya 처럼 링의 앞쪽 반원만 진하게, 뒤쪽 반원은 흐리게 (뒤쪽은 잡히지도 않는다).
            ring = circle(center, side, up, length, segs=64)
            front, back = [], []
            for s in range(len(ring)):
                a0, a1 = ring[s], ring[(s + 1) % len(ring)]
                mid = (a0 + a1) * 0.5 - center
                (front if _ring_front(mid, toward, length) else back).extend((a0, a1))
            if back:
                lines(back, color[:3] + (0.25,), 1.5)
            if front:
                lines(front, color, width)
            continue

        # 화면을 똑바로 향한 축은 Max/Maya 처럼 숨긴다 (점처럼 겹쳐 보여 잘못 잡히기 쉬움).
        if _axis_facing_view(axis, toward) and not scaling and hot != i:
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
        # Max 처럼 축 끝에 X/Y/Z 글자.
        labels.append(("XYZ"[i], tip + axis * (length * 0.12), color))

        if kind == 'MOVE':
            base = tip - axis * (length * 0.18)
            ring = circle(base, side, up, length * 0.06, segs=12)
            tris([p for s in range(12) for p in (tip, ring[s], ring[(s + 1) % 12])], color)
    _state["labels"] = labels

    view_x = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
    view_y = rv3d.view_rotation @ Vector((0.0, 1.0, 0.0))
    white = (1.0, 1.0, 1.0, 0.8)

    def square(c, u, v, half):
        return [c + (u * su + v * sv) * half for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]

    def quad_tris(q):
        return [q[0], q[1], q[2], q[0], q[2], q[3]]

    if kind == 'MOVE':
        # 가운데 사각형 = 화면 평면 자유 이동 (Maya 의 가운데 사각형).
        sq = square(center, view_x, view_y, length * _CENTER_SIZE)
        if hot == H_CENTER:
            tris(quad_tris(sq), (1.0, 1.0, 1.0, 0.25))
        lines(loop(sq), _HIGHLIGHT_COLOR if hot == H_CENTER else white, 2.0)
        # 평면 핸들 = Max 의 모서리 꺾쇠 (두 축 사이 'ㄱ' 모양). 각 변은 나란한 축 색, 마우스를 올리면 노랗게 채운다.
        for n in range(3):
            if _plane_edge_on(rot.col[n], toward) and hot != H_PLANE + n:
                continue
            u_i, v_i = (n + 1) % 3, (n + 2) % 3
            pu, pc, pv, _q = _move_plane_quad(center, rot, length, n)
            h = hot == H_PLANE + n
            if h:
                tris(quad_tris([center, pu, pc, pv]), _HIGHLIGHT_COLOR[:3] + (0.35,))
            lines([pu, pc], _HIGHLIGHT_COLOR if h else _AXIS_COLORS[v_i], 2.0)
            lines([pc, pv], _HIGHLIGHT_COLOR if h else _AXIS_COLORS[u_i], 2.0)

    if kind == 'SCALE':
        # Max 스케일: 가운데 삼각형 = 전체(균일), 바깥 사다리꼴 = 두 축 평면. 마우스를 올리면 노랗게 채운다.
        tri = _scale_center_tri(center, rot, length)
        if hot == H_CENTER:
            tris(tri, _HIGHLIGHT_COLOR[:3] + (0.35,))
        lines(loop(tri), _HIGHLIGHT_COLOR if hot == H_CENTER else (0.85, 0.85, 0.85, 0.9), 1.5)
        for n in range(3):
            if _plane_edge_on(rot.col[n], toward) and hot != H_PLANE + n:
                continue
            q = _scale_plane_quad(center, rot, length, n)
            h = hot == H_PLANE + n
            if h:
                tris(quad_tris(q), _HIGHLIGHT_COLOR[:3] + (0.35,))
            lines([q[1], q[2]], _HIGHLIGHT_COLOR if h else (0.85, 0.85, 0.85, 0.9), 1.5)

    if kind == 'ROTATE':
        # 맥스처럼 피봇 위치에 작은 X/Y/Z 축 표시 (현재 좌표계 기준, 회전 중에도 따라 돈다).
        for i in range(3):
            axis = rot.col[i].normalized()
            lines([center, center + axis * (length * _PIVOT_AXIS_SIZE)], _AXIS_COLORS[i], 2.0)

        # 링 안쪽 = 자유 회전: 마우스를 올리거나 돌리는 중이면 은은하게 채운다.
        if hot == H_TRACKBALL:
            disc = circle(center, view_x, view_y, length * _TRACKBALL_RADIUS)
            tris([p for s in range(len(disc)) for p in (center, disc[s], disc[(s + 1) % len(disc)])],
                 (1.0, 1.0, 1.0, 0.12))
        # 화면 방향 바깥 링 = 화면 축 회전 (기본 회전 기즈모의 흰 링).
        lines(loop(circle(center, view_x, view_y, length * _VIEW_RING_RADIUS)),
              _HIGHLIGHT_COLOR if hot == H_VIEW_RING else (1.0, 1.0, 1.0, 0.6),
              3.5 if hot == H_VIEW_RING else 2.5)

        # Maya/Max 처럼 돌린 만큼을 부채꼴로 채워 보여 준다 (시작 위치 기준).
        r0 = None
        if _state["running"] != 'ROTATE':
            progress = None
        elif own is not None and own["kind"] == 'ROTATE':
            # 애드온 자체 회전: 각도와 잡은 지점(부채꼴 시작)을 연산자가 알려 준다. 자유 회전은 부채꼴 없음.
            progress = None if own["value"] is None else (own["axis"], own["axis_vec"], own["value"])
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
            radius = length * (_VIEW_RING_RADIUS if i == H_VIEW_RING else 1.0)
            arc = [center + (r0 * math.cos(theta * s / steps) + b * math.sin(theta * s / steps)) * radius
                   for s in range(steps + 1)]
            if abs(theta) > 1e-4:
                fill = (_AXIS_COLORS[i][:3] if i < 3 else (1.0, 1.0, 1.0)) + (0.25,)
                tris([p for s in range(steps) for p in (center, arc[s], arc[s + 1])], fill)
            lines([center, arc[0], center, arc[-1]], _HIGHLIGHT_COLOR, 2.0)

        # Max 처럼 잡은 점에 끄는 방향(접선) 화살표를 그린다.
        if own is not None and own["kind"] == 'ROTATE' and own.get("tangent") is not None:
            g, t = own["grab"], own["tangent"]
            half = length * 0.45
            a0, a1 = g - t * half, g + t * half
            lines([a0, a1], _HIGHLIGHT_COLOR, 2.5)
            side_t = own["axis_vec"].cross(t).normalized() * (length * 0.06)
            for tip, sgn in ((a1, 1.0), (a0, -1.0)):
                back = tip - t * (length * 0.1) * sgn
                tris([tip, back + side_t, back - side_t], _HIGHLIGHT_COLOR)

    gpu.state.blend_set('NONE')


def _draw_angle_text():
    """회전 중 각도, 스케일 중 배율 숫자, 축 끝 X/Y/Z 글자 (Max 처럼)."""
    if not _gizmo_enabled():
        return
    context = bpy.context
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return
    font = 0
    labels = _state.get("labels") or []
    if labels:
        blf.size(font, int(13 * context.preferences.system.ui_scale))
        for letter, pos, color in labels:
            p = view3d_utils.location_3d_to_region_2d(region, rv3d, pos)
            if p is None:
                continue
            w, h = blf.dimensions(font, letter)
            blf.color(font, *color)
            blf.position(font, p.x - w * 0.5, p.y - h * 0.5, 0)
            blf.draw(font, letter)
    running = _state["running"]
    if running == 'ROTATE' and _state["angle"] is not None:
        i, deg, center = _state["angle"]
        text = "{:s}  {:+.1f}°".format(("X", "Y", "Z", "View")[i], deg)
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
    """
    기즈모 드래그를 애드온 자체 변환으로 할지. Maya/Max 모드 + 작업 모드 Animation + 오브젝트·포즈·에디트(메쉬, 아마추어) 모드.
    Rigging 작업 모드는 숫자 입력·스냅·비례 편집·미러·Auto IK 같은 Blender 변환 기능이 필요해서 Blender 변환을 쓴다.
    에디트 모드에서도 그 기능이 필요하면 G/R/S 키(Blender 변환)를 쓴다.
    """
    prefs = _prefs()
    if prefs is None or prefs.work_mode != 'ANIMATION' or prefs.nav_mode not in {'MAYA', 'MAX'}:
        return False
    return bpy.context.mode in _GIZMO_MODES


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


_EPS = 1e-6


def _rotation_blocked(owner, axis):
    """회전 잠금 때문에 이 회전을 하면 안 되는지: 회전 채널 전부 잠김, 또는 잡은 링 축의 채널이 잠김."""
    lock = owner.lock_rotation
    if owner.rotation_mode == 'QUATERNION' and owner.lock_rotations_4d and not owner.lock_rotation_w:
        return False
    if all(lock):
        return True
    return axis < 3 and lock[axis]


def _keep_or(start_v, new_v, locked):
    """잠겼거나 사실상 안 바뀐(부동소수 오차 수준) 값은 처음 값을 그대로 쓴다."""
    if locked or abs(new_v - start_v) < _EPS:
        return start_v
    return new_v


def _apply_basis(owner, basis, start, prev_rot, keep=()):
    """
    분해한 basis 행렬을 loc/rot/scale 채널에 넣는다. 잠긴 채널과 이번 변환 종류와 관계없는 채널(keep)은
    처음 값을 그대로 두고(이동 중 회전·스케일에 오차가 새지 않게), 오일러는 직전 값과 연속이 되게 넣는다.
    넣은 회전값을 돌려준다.
    """
    loc, quat, scale = basis.decompose()
    start_loc, start_rot, start_scale = start
    if 'loc' not in keep:
        owner.location = [_keep_or(start_loc[i], loc[i], owner.lock_location[i]) for i in range(3)]
    if 'scale' not in keep:
        owner.scale = [_keep_or(start_scale[i], scale[i], owner.lock_scale[i]) for i in range(3)]
    mode = owner.rotation_mode
    if 'rot' in keep:
        return prev_rot
    if mode == 'QUATERNION':
        if quat.dot(prev_rot) < 0.0:
            quat.negate()
        lock = owner.lock_rotation
        if all(lock) and (not owner.lock_rotations_4d or owner.lock_rotation_w):
            return prev_rot
        q = [quat.w, quat.x, quat.y, quat.z]
        s = [start_rot.w, start_rot.x, start_rot.y, start_rot.z]
        if owner.lock_rotations_4d and owner.lock_rotation_w:
            q[0] = s[0]
        for i in range(3):
            if lock[i]:
                q[i + 1] = s[i + 1]
        quat = Quaternion(q).normalized()
        owner.rotation_quaternion = quat
        return quat.copy()
    if mode == 'AXIS_ANGLE':
        axis, angle = quat.to_axis_angle()
        owner.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
        return quat.copy()
    eul = quat.to_euler(mode, prev_rot)
    owner.rotation_euler = [_keep_or(start_rot[i], eul[i], owner.lock_rotation[i]) for i in range(3)]
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
    """Move/rotate/scale along the grabbed DCC gizmo handle (without constraint lines)"""
    bl_idname = "dccnav.gizmo_transform"
    bl_label = "DCC Gizmo Transform"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    kind: bpy.props.EnumProperty(
        items=(('MOVE', "Move", ""), ('ROTATE', "Rotate", ""), ('SCALE', "Scale", "")),
        options={'SKIP_SAVE'},
    )
    # 0~2 = X/Y/Z 축. 그 밖은 핸들: H_CENTER(이동=화면 평면, 스케일=전체), H_VIEW_RING(회전 바깥 링),
    # H_TRACKBALL(회전 링 안쪽 = 자유 회전), H_PLANE+i(이동 평면 핸들, i 축이 법선).
    axis: bpy.props.IntProperty(min=0, max=6, options={'SKIP_SAVE'})
    from_mmb: bpy.props.BoolProperty(options={'SKIP_SAVE', 'HIDDEN'})

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
                    world = o.matrix_world.copy()
                    basis = o.matrix_basis.copy()
                    self.objects.append({
                        "obj": o,
                        "world": world,
                        "basis": basis,
                        # 부모·컨스트레인트를 합친 '실제 부모' 행렬 (world = peff @ basis).
                        "peff": world @ basis.inverted_safe(),
                        "start": (o.location.copy(), _rot_value(o), o.scale.copy()),
                        "prev": _rot_value(o),
                    })
        elif mode == 'POSE':
            sel = set(context.selected_pose_bones or [])
            # 개별 원점 회전은 자식도 각자 자기 축으로 돈다 (Max 바이패드 스파인 여러 개 회전과 같음).
            # 이동·스케일은 부모를 따라 두 번 움직이지 않게 맨 위 본만 변환한다.
            keep_children = (self.kind == 'ROTATE'
                             and context.scene.tool_settings.transform_pivot_point == 'INDIVIDUAL_ORIGINS')

            def has_selected_parent(pb):
                p = pb.parent
                while p is not None:
                    if p in sel:
                        return True
                    p = p.parent
                return False

            for pb in sel:
                if keep_children or not has_selected_parent(pb):
                    arm = pb.id_data
                    world = arm.matrix_world @ pb.matrix
                    basis = pb.matrix_basis.copy()
                    self.bones.append({
                        "pb": pb,
                        "arm": arm,
                        "world": world,
                        "basis": basis,
                        # 부모·컨스트레인트(Rigify 부모 전환 등)를 합친 '실제 부모' 행렬 (world = peff @ basis).
                        "peff": world @ basis.inverted_safe(),
                        "start": (pb.location.copy(), _rot_value(pb), pb.scale.copy()),
                        "prev": _rot_value(pb),
                    })
        elif mode == 'EDIT_MESH':
            # 같이 편집 중인 오브젝트마다 선택된 점과 그 월드 위치.
            self.verts = []
            for obj in context.objects_in_mode_unique_data:
                bm = bmesh.from_edit_mesh(obj.data)
                mw = obj.matrix_world
                items = [(v, mw @ v.co) for v in bm.verts if v.select]
                if items:
                    # bm 을 들고 있어야 BMVert 가 살아 있다.
                    self.verts.append({"obj": obj, "bm": bm, "imw": mw.inverted(), "items": items})
        elif mode == 'EDIT_ARMATURE':
            self.ebones = []
            for obj in context.objects_in_mode_unique_data:
                mw = obj.matrix_world
                items = []
                for eb in obj.data.edit_bones:
                    # 에디트 본 Lock 이 켜진 본은 Blender 변환처럼 움직이지 않는다.
                    if (eb.select_head or eb.select_tail) and not eb.lock:
                        items.append((eb, eb.select_head, eb.select_tail, mw @ eb.head, mw @ eb.tail,
                                      eb.roll, mw @ eb.matrix))
                if items:
                    self.ebones.append({"imw": mw.inverted(), "items": items})

    def _has_targets(self):
        return bool(self.objects or self.bones or self.verts or self.ebones)

    # --- 적용 ---------------------------------------------------------------

    def _apply(self, context, delta):
        # delta 는 공통 행렬, 또는 대상마다 행렬을 돌려주는 함수(개별 원점 피봇).
        # 월드에서의 변화(d)를 '실제 부모' 기준으로 바꿔 채널(basis)에 곱한다:
        #   새 basis = peff⁻¹ · d · peff · 처음 basis
        # 결과 월드 행렬을 다시 채널로 역산하지 않으므로, 컨스트레인트가 걸린 컨트롤러(Rigify IK 등)에서도
        # 잠긴 채널·바뀌지 않아야 할 채널에 미세한 오차가 새어 들어가지 않는다.
        per_item = callable(delta)
        # 이동은 회전·스케일을, 회전은 스케일을, 스케일은 회전을 건드리지 않는다 (Blender 변환과 같음).
        keep = {'MOVE': ('rot', 'scale'), 'ROTATE': ('scale',), 'SCALE': ('rot',)}[self.kind]
        for item in self.objects + self.bones:
            owner = item["obj"] if "obj" in item else item["pb"]
            if self.kind == 'ROTATE' and _rotation_blocked(owner, self.axis):
                # 잠긴 축의 링을 돌리면 아예 돌지 않는다 (Max 와 같음). 그냥 돌린 뒤 잠긴 채널만 되돌리면
                # 그 회전이 잠기지 않은 다른 채널로 새어 들어가 컨트롤러가 미묘하게 움직인다.
                continue
            d = delta(item) if per_item else delta
            peff = item["peff"]
            basis = peff.inverted_safe() @ d @ peff @ item["basis"]
            item["prev"] = _apply_basis(owner, basis, item["start"], item["prev"], keep)
        # 에디트 모드는 개별 원점 없이 공통 행렬만 온다 (invoke 에서 individual 을 끈다).
        for group in self.verts or ():
            m = group["imw"] @ delta
            for v, co in group["items"]:
                v.co = m @ co
            bmesh.update_edit_mesh(group["obj"].data, loop_triangles=False, destructive=False)
        for group in self.ebones or ():
            imw = group["imw"]
            for eb, sh, st, head, tail, roll, world in group["items"]:
                if sh and st:
                    # 본 전체가 선택됨: 머리·꼬리·롤을 같이 변환한다 (본 축으로 돌리면 롤이 바뀐다).
                    m = imw @ delta @ world
                    full = m.to_3x3().normalized().to_4x4()
                    full.translation = m.translation
                    eb.matrix = full
                    eb.tail = imw @ (delta @ tail)
                    continue
                eb.head = imw @ (delta @ head) if sh else imw @ head
                eb.tail = imw @ (delta @ tail) if st else imw @ tail
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
            if self.axis < 3:
                t = _closest_axis_param(self.center, self.a, o, d)
                if t is None:
                    return self.value
                value = t - self.t0
                if precise:
                    value *= 0.1
                if snap:
                    step = 0.1 * (context.scene.unit_settings.scale_length or 1.0)
                    value = round(value / step) * step
                return value
            # 가운데 사각형(화면 평면) / 평면 핸들: 그 평면 위에서 자유 이동.
            hit = _ray_plane_hit(o, d, self.center, self.plane_n)
            if hit is None:
                return self.value
            vec = hit - self.hit0
            if precise:
                vec *= 0.1
            if snap:
                step = 0.1 * (context.scene.unit_settings.scale_length or 1.0)
                vec = Vector([round(c / step) * step for c in vec])
            return vec
        if self.kind == 'ROTATE':
            if self.axis == H_TRACKBALL:
                # 자유 회전: 누른 지점에서 지금 마우스까지의 '총 이동량'으로만 정한다 (화면 좌우 = 세로축, 상하 = 가로축).
                # 지나온 경로는 따지지 않으므로, 제자리에서 마우스를 빙빙 돌려도 계속 돌지 않고
                # 처음 누른 자리로 돌아오면 처음 방향으로 돌아온다.
                d = mouse - self.press_mouse
                k = 0.01 * (0.1 if precise else 1.0)
                self.q_total = Quaternion(self.view_y, d.x * k) @ Quaternion(self.view_x, -d.y * k)
                return self.q_total.angle
            # Max/Maya 방식: 잡은 지점에서 링의 접선 방향으로 끈 거리만큼 돈다.
            # 접선 방향으로 링 반지름(px)만큼 끌면 1 라디안 = 잡은 점이 처음엔 마우스를 그대로 따라온다.
            moved = (mouse - self.press_mouse).dot(self.tangent2d)
            value = moved / self.ring_px * (0.1 if precise else 1.0)
            if snap:
                step = math.radians(5.0)
                value = round(value / step) * step
            return value
        # SCALE: Maya 처럼 끈 거리에 비례한다. 기즈모 길이(px)/감도 만큼 끌면 +1배.
        # (잡은 지점까지 거리의 비율로 하면 가운데 근처를 잡았을 때 지나치게 민감해진다.)
        if self.axis == H_CENTER:
            # 가운데 핸들: 전체(균일) 스케일. Max 처럼 누른 자리에서 위·오른쪽으로 끌면 커지고 아래·왼쪽이면 작아진다.
            d = mouse - self.press_s
            moved = d.x + d.y
        else:
            moved = (mouse - self.c2d).dot(self.s2d) - self.d0
        factor = max(1.0 + moved / self.ref, 0.001)
        if precise:
            factor = 1.0 + (factor - 1.0) * 0.1
        if snap:
            factor = round(factor / 0.1) * 0.1
        return factor

    def _delta(self):
        c, a = self.center, self.a
        to_c = Matrix.Translation(c)
        from_c = Matrix.Translation(-c)
        if self.kind == 'MOVE':
            if self.axis < 3:
                return Matrix.Translation(a * self.value)
            return Matrix.Translation(self.value)
        if self.kind == 'ROTATE':
            if self.axis == H_TRACKBALL:
                return to_c @ self.q_total.to_matrix().to_4x4() @ from_c
            return to_c @ Matrix.Rotation(self.value, 4, a) @ from_c
        f = self.value
        if self.axis == H_CENTER:
            return to_c @ Matrix.Scale(f, 4) @ from_c
        s3 = Matrix.Identity(3)
        if self.axis >= H_PLANE:
            # 평면 스케일: 법선 축만 그대로 두고 나머지 두 축을 같이 늘린다.
            n = self.plane_n
            for r in range(3):
                for col in range(3):
                    s3[r][col] += (f - 1.0) * ((1.0 if r == col else 0.0) - n[r] * n[col])
        else:
            for r in range(3):
                for col in range(3):
                    s3[r][col] += (f - 1.0) * a[r] * a[col]
        return to_c @ s3.to_4x4() @ from_c

    def _item_axis(self, item, idx):
        """개별 원점에서 쓸 축: Local 계열 좌표계면 대상 자신의 축, 아니면 공통 기즈모 축."""
        if self.local_axes:
            return item["world"].to_3x3().normalized().col[idx].normalized()
        return self.rot.col[idx].normalized()

    def _delta_for(self, item):
        """
        개별 원점(Individual Origins) 피봇: 대상마다 자기 원점(오브젝트 원점 / 본 머리)을 중심으로,
        Local 이면 자기 축으로 변환한다 (손가락 여러 개를 한 번에 말 때처럼).
        """
        c = item["world"].translation.copy()
        to_c = Matrix.Translation(c)
        from_c = Matrix.Translation(-c)
        if self.kind == 'MOVE':
            if self.axis < 3:
                return Matrix.Translation(self._item_axis(item, self.axis) * self.value)
            return Matrix.Translation(self.value)
        if self.kind == 'ROTATE':
            if self.axis == H_TRACKBALL:
                return to_c @ self.q_total.to_matrix().to_4x4() @ from_c
            if self.axis < 3:
                a = self._item_axis(item, self.axis)
                # 대상 축이 기즈모 축과 반대 방향이면 같은 쪽으로 돌도록 부호를 맞춘다.
                angle = self.value if a.dot(self.a) >= 0.0 else -self.value
            else:
                a, angle = self.a, self.value
            return to_c @ Matrix.Rotation(angle, 4, a) @ from_c
        f = self.value
        if self.axis == H_CENTER:
            return to_c @ Matrix.Scale(f, 4) @ from_c
        s3 = Matrix.Identity(3)
        if self.axis >= H_PLANE:
            n = self._item_axis(item, self.axis - H_PLANE)
            for r in range(3):
                for col in range(3):
                    s3[r][col] += (f - 1.0) * ((1.0 if r == col else 0.0) - n[r] * n[col])
        else:
            a = self._item_axis(item, self.axis)
            for r in range(3):
                for col in range(3):
                    s3[r][col] += (f - 1.0) * a[r] * a[col]
        return to_c @ s3.to_4x4() @ from_c

    def _publish(self):
        factors = [1.0, 1.0, 1.0]
        if self.kind == 'SCALE':
            if self.axis == H_CENTER:
                factors = [self.value] * 3
            elif self.axis >= H_PLANE:
                factors = [self.value] * 3
                factors[self.axis - H_PLANE] = 1.0
            else:
                factors[self.axis] = self.value
        rotating_axis = self.kind == 'ROTATE' and self.axis != H_TRACKBALL
        _state["op"] = {
            "kind": self.kind,
            "axis": self.axis,
            "axis_vec": self.a.copy(),
            "value": self.value if rotating_axis else None,
            "r0": self.r0,
            "factors": tuple(factors),
            # 링을 잡은 점과 끄는 방향(접선). Max 처럼 화면에 표시한다.
            "grab": getattr(self, "grab3d", None) if rotating_axis else None,
            "tangent": getattr(self, "tangent3d", None) if rotating_axis else None,
        }

    def _header(self, context):
        if self.kind == 'MOVE':
            if self.axis < 3:
                text = "Move {:s}: {:.4f}".format("XYZ"[self.axis], self.value)
            else:
                name = "View Plane" if self.axis == H_CENTER else ("YZ", "XZ", "XY")[self.axis - H_PLANE]
                text = "Move {:s}: {:.4f}  {:.4f}  {:.4f}".format(name, *self.value)
        elif self.kind == 'ROTATE':
            if self.axis == H_TRACKBALL:
                text = "Free Rotate: {:.2f}°".format(math.degrees(self.value))
            else:
                name = "View" if self.axis == H_VIEW_RING else "XYZ"[self.axis]
                text = "Rotate {:s}: {:+.2f}°".format(name, math.degrees(self.value))
        else:
            if self.axis == H_CENTER:
                name = "Uniform"
            elif self.axis >= H_PLANE:
                name = ("YZ", "XZ", "XY")[self.axis - H_PLANE]
            else:
                name = "XYZ"[self.axis]
            text = "Scale {:s}: ×{:.3f}".format(name, self.value)
        text += "    Ctrl: Snap   Shift: Precision   RMB/Esc: Cancel"
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
        region = context.region
        rv3d = context.region_data
        # 기즈모는 누른 순간(PRESS) 시작된다. Blender 가 고른 부분과 우리 판정이 어긋나는 일이 없도록
        # 누른 위치를 같은 판정(_move_pick / _rotate_pick / _scale_pick)으로 다시 확인해서 그 부분을 쓴다.
        # 가운데 버튼 조작(Maya)으로 불렸으면 활성 핸들을 그대로 쓴다 (마우스가 기즈모 위가 아니어도 된다).
        self.button = 'MIDDLEMOUSE' if self.from_mmb else 'LEFTMOUSE'
        if self.kind in _PICKERS and not self.from_mmb:
            part = _PICKERS[self.kind](context, (event.mouse_region_x, event.mouse_region_y))
            _debug_invoke.clear()
            _debug_invoke.update({"given_axis": self.axis, "pick": part,
                                  "mouse": (event.mouse_region_x, event.mouse_region_y)})
            if part is not None:
                self.axis = part
            # Maya 처럼 마지막으로 잡은 핸들을 활성 핸들로 기억한다 (가운데 버튼 조작에 쓴다).
            _state["active"][self.kind] = self.axis
        view_x = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
        view_y = rv3d.view_rotation @ Vector((0.0, 1.0, 0.0))
        toward = rv3d.view_rotation @ Vector((0.0, 0.0, 1.0))
        self.center = center
        self.rot = rot
        # 개별 원점 피봇: 각자 제자리에서 변환. 기즈모는 Blender 처럼 선택 중간점에 둔다.
        # 에디트 모드는 개별 원점도 선택 중간점 기준으로 한 번에 변환한다.
        self.individual = (context.scene.tool_settings.transform_pivot_point == 'INDIVIDUAL_ORIGINS'
                           and not self.verts and not self.ebones)
        self.local_axes = context.scene.transform_orientation_slots[0].type in {'LOCAL', 'GIMBAL', 'NORMAL'}
        if self.axis < 3:
            self.a = rot.col[self.axis].normalized()
        else:
            self.a = toward.normalized()
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        self.c2d = view3d_utils.location_3d_to_region_2d(region, rv3d, center) or mouse.copy()
        self.r0 = None

        if self.kind == 'MOVE':
            o, d = self._ray(context, mouse)
            if self.axis < 3:
                t0 = _closest_axis_param(center, self.a, o, d)
                if t0 is None:
                    return {'CANCELLED'}
                self.t0 = t0
                self.value = 0.0
            else:
                if self.axis == H_CENTER:
                    self.plane_n = toward.normalized()
                else:
                    self.plane_n = rot.col[self.axis - H_PLANE].normalized()
                hit0 = _ray_plane_hit(o, d, center, self.plane_n)
                if hit0 is None:
                    return {'CANCELLED'}
                self.hit0 = hit0
                self.value = Vector((0.0, 0.0, 0.0))
        elif self.kind == 'ROTATE':
            if self.axis == H_TRACKBALL:
                self.view_x = view_x.normalized()
                self.view_y = view_y.normalized()
                self.press_mouse = mouse.copy()
                self.q_total = Quaternion()
                self.value = 0.0
            else:
                # 잡은 지점: 화면에 그려진 링에서 마우스와 가장 가까운 점 (같은 판정으로 앞쪽 반원 우선).
                frame = _gizmo_frame(context)
                length = frame[2] if frame is not None else 1.0
                radius = length * (_VIEW_RING_RADIUS if self.axis == H_VIEW_RING else 1.0)
                if self.axis == H_VIEW_RING:
                    side, up = view_x.normalized(), view_y.normalized()
                else:
                    side, up, _ax = _axis_basis(rot, self.axis)
                best = None
                for s in range(96):
                    t = s / 96 * 2.0 * math.pi
                    r = side * math.cos(t) + up * math.sin(t)
                    p = view3d_utils.location_3d_to_region_2d(region, rv3d, center + r * radius)
                    if p is None:
                        continue
                    score = (p - mouse).length + (4.0 if r.dot(toward) < 0.0 else 0.0)
                    if best is None or score < best[0]:
                        best = (score, r, p)
                self.r0 = best[1].normalized() if best else (side if side.length else Vector((1, 0, 0)))
                grab = center + self.r0 * radius
                grab2d = best[2] if best else mouse.copy()
                # 접선 = 회전축 × 반지름 방향: + 방향으로 끌면 + 회전.
                tangent = self.a.cross(self.r0).normalized()
                tip = view3d_utils.location_3d_to_region_2d(region, rv3d, grab + tangent * radius * 0.2)
                t2d = (tip - grab2d) if tip is not None else Vector((0.0, 0.0))
                if t2d.length < 2.0:
                    # 링을 옆에서 봐서 접선이 화면 깊이 방향이면, 화면에서 반지름에 수직인 방향을 쓴다.
                    radial = grab2d - self.c2d
                    t2d = Vector((-radial.y, radial.x)) * (1.0 if self.a.dot(toward) >= 0.0 else -1.0)
                self.tangent2d = t2d.normalized() if t2d.length > 1e-6 else Vector((1.0, 0.0))
                self.grab3d, self.tangent3d = grab, tangent
                self.ring_px = max(_prefs().axis_overlay_size * (radius / length), 10.0)
                self.press_mouse = mouse.copy()
                self.value = 0.0
        else:
            if self.axis == H_CENTER:
                self.press_s = mouse.copy()
            else:
                direction = self.a
                if self.axis >= H_PLANE:
                    n = self.axis - H_PLANE
                    self.plane_n = rot.col[n].normalized()
                    # 평면 핸들: 그 평면의 대각선 방향으로 끌면 늘어난다.
                    direction = (rot.col[(n + 1) % 3].normalized() + rot.col[(n + 2) % 3].normalized()).normalized()
                tip2d = view3d_utils.location_3d_to_region_2d(region, rv3d, center + direction)
                if tip2d is None or (tip2d - self.c2d).length < 1e-3:
                    return {'CANCELLED'}
                self.s2d = (tip2d - self.c2d).normalized()
                self.d0 = (mouse - self.c2d).dot(self.s2d)
            prefs = _prefs()
            self.ref = prefs.axis_overlay_size / max(prefs.scale_sensitivity, 0.01)
            self.value = 1.0

        _state["drag"] = self.axis
        self._publish()
        self._header(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_SHIFT', 'RIGHT_SHIFT'}:
            self.value = self._value(context, event)
            self._apply(context, self._delta_for if self.individual else self._delta())
            self._publish()
            self._header(context)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == self.button and event.value == 'RELEASE':
            self._finish(context)
            self._autokey(context)
            return {'FINISHED'}
        if event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self._restore(context)
            self._finish(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        # 디버그: 마지막 변환값 (회전은 라디안, 자유 회전은 누적 각도).
        _debug_invoke["final_kind"] = self.kind
        _debug_invoke["final_axis"] = self.axis
        _debug_invoke["final_value"] = self.value if not isinstance(self.value, Vector) else tuple(self.value)
        _state["op"] = None
        context.area.header_text_set(None)
        context.area.tag_redraw()


def _dist_point_segment(p, a, b):
    ab = b - a
    denom = ab.dot(ab)
    if denom < 1e-12:
        return (p - a).length
    t = max(0.0, min(1.0, (p - a).dot(ab) / denom))
    return (p - (a + ab * t)).length


def _rotate_pick(context, location):
    """
    회전 기즈모에서 마우스가 가리키는 부분: 0~2 = X/Y/Z 링, H_VIEW_RING = 흰 바깥 링,
    H_TRACKBALL = 링 안쪽(자유 회전), None = 없음.
    화면에 그려진 링 선까지 거리로 직접 판정한다. 링이 겹치면 더 가까운 링, 같으면 앞쪽(보는 쪽) 반원을 고른다.
    """
    frame = _gizmo_frame(context)
    if frame is None:
        return None
    center, rot, length = frame
    region = context.region
    rv3d = context.region_data
    c2d = view3d_utils.location_3d_to_region_2d(region, rv3d, center)
    if c2d is None:
        return None
    mouse = Vector(location)
    size = _prefs().axis_overlay_size
    tol = 10.0 * context.preferences.system.ui_scale
    toward = rv3d.view_rotation @ Vector((0.0, 0.0, 1.0))
    segs = 64
    best = None
    for i in range(3):
        side, up, _axis = _axis_basis(rot, i)
        prev = None
        for s in range(segs + 1):
            t = s / segs * 2.0 * math.pi
            w = center + (side * math.cos(t) + up * math.sin(t)) * length
            p = view3d_utils.location_3d_to_region_2d(region, rv3d, w)
            if p is not None and prev is not None and _ring_front(w - center, toward, length):
                # 뒤쪽 반원은 흐리게만 그리고 잡히지 않는다 (Max/Maya 와 같음).
                d = _dist_point_segment(mouse, prev, p)
                if d < tol and (best is None or d < best[0]):
                    best = (d, i)
            prev = p
    if best is not None:
        return best[1]
    r = (mouse - c2d).length
    if abs(r - size * _VIEW_RING_RADIUS) < tol:
        return H_VIEW_RING
    if r <= size * _TRACKBALL_RADIUS:
        return H_TRACKBALL
    return None


def _poly_contains(poly, p):
    """화면 좌표 볼록 다각형 안에 점이 있는지."""
    sign = 0.0
    n = len(poly)
    for k in range(n):
        a, b = poly[k], poly[(k + 1) % n]
        cr = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
        if abs(cr) < 1e-9:
            continue
        if sign == 0.0:
            sign = cr
        elif (cr > 0.0) != (sign > 0.0):
            return False
    return True


def _pick_setup(context, location):
    frame = _gizmo_frame(context)
    if frame is None:
        return None
    region, rv3d = context.region, context.region_data
    c2 = view3d_utils.location_3d_to_region_2d(region, rv3d, frame[0])
    if c2 is None:
        return None

    def proj(w):
        return view3d_utils.location_3d_to_region_2d(region, rv3d, w)

    toward = rv3d.view_rotation @ Vector((0.0, 0.0, 1.0))
    tol = 8.0 * context.preferences.system.ui_scale
    return frame, c2, proj, toward, Vector(location), tol


def _pick_axis(frame, c2, proj, toward, mouse, tol, start=0.0):
    """화면에 그려진 축 선(start~끝)에 가장 가까운 축. 화면을 향한 축은 제외."""
    center, rot, length = frame
    best = None
    for i in range(3):
        axis = rot.col[i].normalized()
        if _axis_facing_view(axis, toward):
            continue
        a = proj(center + axis * (length * start)) if start > 0.0 else c2
        b = proj(center + axis * length * 1.1)
        if a is None or b is None:
            continue
        d = _dist_point_segment(mouse, a, b)
        if d < tol and (best is None or d < best[0]):
            best = (d, i)
    return best[1] if best else None


def _pick_plane(frame, proj, toward, mouse, quad_fn):
    """평면 핸들 영역 안인지. 여러 개면 화면을 가장 정면으로 향한 평면."""
    center, rot, length = frame
    best = None
    for n in range(3):
        if _plane_edge_on(rot.col[n], toward):
            continue
        poly = [proj(w) for w in quad_fn(center, rot, length, n)]
        if any(p is None for p in poly) or not _poly_contains(poly, mouse):
            continue
        facing = abs(rot.col[n].normalized().dot(toward))
        if best is None or facing > best[0]:
            best = (facing, H_PLANE + n)
    return best[1] if best else None


def _move_pick(context, location):
    """이동 기즈모(Max 방식)에서 마우스가 가리키는 부분: 가운데 사각형 > 축 > 평면 꺾쇠."""
    s = _pick_setup(context, location)
    if s is None:
        return None
    frame, c2, proj, toward, mouse, tol = s
    half = _prefs().axis_overlay_size * _CENTER_SIZE + 2.0
    if abs(mouse.x - c2.x) <= half and abs(mouse.y - c2.y) <= half:
        return H_CENTER
    axis = _pick_axis(frame, c2, proj, toward, mouse, tol, start=_CENTER_SIZE)
    if axis is not None:
        return axis
    return _pick_plane(frame, proj, toward, mouse, lambda c, r, l, n: _move_plane_quad(c, r, l, n)[3])


def _scale_pick(context, location):
    """스케일 기즈모(Max 방식): 축 바깥쪽 > 가운데 삼각형(전체) > 사다리꼴(두 축)."""
    s = _pick_setup(context, location)
    if s is None:
        return None
    frame, c2, proj, toward, mouse, tol = s
    axis = _pick_axis(frame, c2, proj, toward, mouse, tol, start=_SCALE_OUTER)
    if axis is not None:
        return axis
    center, rot, length = frame
    # 가운데(전체 스케일): 그려진 삼각형 안 + 중심에서 삼각형 꼭짓점 거리(화면 px) 안쪽 원 전체.
    # 축이 화면을 향하면 삼각형이 납작해져 잡기 어려우므로 원으로 넓힌다 (사다리꼴은 그 바깥이라 겹치지 않음).
    if (mouse - c2).length <= _prefs().axis_overlay_size * _SCALE_INNER:
        return H_CENTER
    tri = [proj(w) for w in _scale_center_tri(center, rot, length)]
    if all(p is not None for p in tri) and _poly_contains(tri, mouse):
        return H_CENTER
    return _pick_plane(frame, proj, toward, mouse, _scale_plane_quad)


_PICKERS = {'MOVE': _move_pick, 'ROTATE': _rotate_pick, 'SCALE': _scale_pick}


def _make_part_gizmo(kind, part):
    """기즈모의 한 부분(판정 결과가 part 일 때만 잡힘)을 잡는 영역 기즈모 클래스. 그리기는 _draw_gizmo 가 한다."""
    pick = _PICKERS[kind]

    def test_select(self, context, location):
        return 0 if pick(context, location) == part else -1

    name = "DCCNAV_GT_{:s}_part{:d}".format(kind.lower(), part)
    return type(
        name,
        (bpy.types.Gizmo,),
        {
            "__doc__": "DCC {:s} gizmo hit area (part {:d})".format(kind.lower(), part),
            "bl_idname": name,
            "draw": lambda self, _context: None,
            "test_select": test_select,
        },
    )


# 부분 번호 = 연산자 axis 번호. 같은 종류 안에서는 같은 판정을 써서 서로 겹치지 않는다.
#   이동: 0~2 축, 3 가운데(화면 평면), 4~6 평면 꺾쇠
#   회전: 0~2 링, 3 흰 바깥 링, 4 링 안쪽(자유 회전)
#   스케일: 0~2 축, 3 가운데 삼각형(전체), 4~6 사다리꼴(두 축)
_PART_COUNTS = {'MOVE': 7, 'ROTATE': 5, 'SCALE': 7}
_PART_GIZMOS = {kind: tuple(_make_part_gizmo(kind, p) for p in range(count))
                for kind, count in _PART_COUNTS.items()}


class DCCNAV_OT_mmb_manipulate(bpy.types.Operator):
    """Maya: drag with the middle mouse button anywhere to move/rotate/scale with the active gizmo handle"""
    bl_idname = "dccnav.mmb_manipulate"
    bl_label = "DCC Middle-Mouse Manipulate"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        # 커스텀 기즈모가 보이는 곳(애니메이션·오브젝트/포즈·이동/회전/스케일 툴)에서만. 아니면 아무 동작 안 함.
        return (context.region_data is not None and _custom_gizmo_here(context)
                and _active_tool_kind(context) is not None and _own_transform_on())

    def invoke(self, context, _event):
        kind = _active_tool_kind(context)
        axis = _state["active"].get(kind, 3)
        res = bpy.ops.dccnav.gizmo_transform('INVOKE_DEFAULT', kind=kind, axis=axis, from_mmb=True)
        # 실제 변환은 gizmo_transform 이 모달로 이어 간다. 이 연산자는 시작만 하고 끝낸다.
        return {'FINISHED'} if res & {'RUNNING_MODAL', 'FINISHED'} else {'PASS_THROUGH'}


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
        def new(gtype, **kw):
            gz = self.gizmos.new(gtype)
            for name, value in kw.items():
                setattr(gz, name, value)
            gz.use_draw_scale = False
            # 보이는 모양은 _draw_gizmo 가 그린다. 이 기즈모는 잡는 영역만 제공한다.
            gz.alpha = 0.0
            gz.alpha_highlight = 0.0
            return gz

        # 각 목록의 번호 = 연산자 axis 번호 (0~2 축, 3 이후 핸들).
        # 모든 부분을 직접 판정하는 영역 기즈모로 만든다 (Max 모양과 잡는 영역이 정확히 같게).
        self.handles = {kind: [new(cls.bl_idname) for cls in classes]
                        for kind, classes in _PART_GIZMOS.items()}
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
                # Blender 기본 모드: Blender 변환 연산자를 그대로 쓴다.
                if kind == 'ROTATE' and i == H_TRACKBALL:
                    props = gz.target_set_operator("transform.trackball")
                    props.release_confirm = True
                    continue
                if kind == 'MOVE':
                    props = gz.target_set_operator("transform.translate")
                elif kind == 'ROTATE':
                    props = gz.target_set_operator("transform.rotate")
                else:
                    props = gz.target_set_operator("transform.resize")
                if i < 3:
                    axis = [False, False, False]
                    axis[i] = True
                    props.constraint_axis = axis
                    props.orient_type = orient
                    if kind == 'ROTATE':
                        props.orient_axis = "XYZ"[i]
                elif kind in {'MOVE', 'SCALE'} and i >= H_PLANE:
                    axis = [True, True, True]
                    axis[i - H_PLANE] = False
                    props.constraint_axis = axis
                    props.orient_type = orient
                elif kind == 'ROTATE':
                    props.orient_type = 'VIEW'
                    props.orient_axis = 'Z'
                    props.constraint_axis = (False, False, True)
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
        view_rot = context.region_data.view_rotation.to_matrix().to_4x4()
        toward = context.region_data.view_rotation @ Vector((0.0, 0.0, 1.0))
        for k, items in self.handles.items():
            for i, gz in enumerate(items):
                visible = frame is not None and k == kind
                if visible and k in {'MOVE', 'SCALE'}:
                    # 화면을 향한 축 핸들, 옆으로 누운 평면 핸들은 숨긴다 (그리기와 같은 규칙).
                    if i < 3 and _axis_facing_view(frame[1].col[i], toward):
                        visible = False
                    elif i >= H_PLANE and _plane_edge_on(frame[1].col[i - H_PLANE], toward):
                        visible = False
                gz.hide = not visible
                if not visible:
                    continue
                center, rot, length = frame
                to_c = Matrix.Translation(center)
                if i < 3:
                    side, up, axis = _axis_basis(rot, i)
                    basis = Matrix((side, up, axis)).transposed().to_4x4()
                    gz.matrix_basis = to_c @ basis @ Matrix.Scale(length, 4)
                elif k == 'ROTATE':
                    radius = _VIEW_RING_RADIUS if i == H_VIEW_RING else _TRACKBALL_RADIUS
                    gz.matrix_basis = to_c @ view_rot @ Matrix.Scale(length * radius, 4)
                elif k in {'MOVE', 'SCALE'} and i >= H_PLANE:
                    n = i - H_PLANE
                    u = rot.col[(n + 1) % 3].normalized()
                    v = rot.col[(n + 2) % 3].normalized()
                    pos = center + (u + v) * (length * _PLANE_OFFSET)
                    basis = Matrix((u, v, rot.col[n].normalized())).transposed().to_4x4()
                    gz.matrix_basis = Matrix.Translation(pos) @ basis @ Matrix.Scale(length * _PLANE_SIZE, 4)
                else:
                    gz.matrix_basis = to_c @ Matrix.Scale(length * _CENTER_SIZE, 4)
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
    DCCNAV_OT_apply_mode,
    DCCNAV_OT_gizmo_size,
    VIEW3D_PT_dcc_navigation,
    DCCNAV_OT_gizmo_transform,
    DCCNAV_OT_mmb_manipulate,
    *_PART_GIZMOS['MOVE'],
    *_PART_GIZMOS['ROTATE'],
    *_PART_GIZMOS['SCALE'],
    VIEW3D_GGT_dcc_navigation,
)


# -----------------------------------------------------------------------------
# 기본 피봇

def _set_default_pivot():
    """기본 피봇을 개별 원점(Individual Origins)으로. 파일을 열거나 새로 만들 때 한 번 맞추고, 그 뒤 변경은 사용자 몫."""
    prefs = _prefs()
    if prefs is None or not prefs.default_individual_pivot:
        return
    for scene in bpy.data.scenes:
        scene.tool_settings.transform_pivot_point = 'INDIVIDUAL_ORIGINS'


@bpy.app.handlers.persistent
def _on_load_post(_dummy):
    _set_default_pivot()


def _default_pivot_timer():
    _set_default_pivot()
    return None  # 1회 실행.


# -----------------------------------------------------------------------------
# 등록

def register():
    global _draw_handle, _text_handle
    for cls in _classes:
        bpy.utils.register_class(cls)
    prefs = _prefs()
    if prefs is not None:
        # 패널의 '고른 값'을 현재 적용된 값과 맞춰 둔다.
        prefs.pending_nav_mode = prefs.nav_mode
        prefs.pending_work_mode = prefs.work_mode
    mode = _current_mode()
    _register_keymaps(mode, _hotkeys_on())
    if bpy.context.window_manager.keyconfigs.user is not None:
        _disable_conflicts()
    bpy.app.timers.register(_startup_apply, first_interval=1.0)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    # 등록 중에는 bpy.data 에 접근할 수 없어서, 지금 열린 파일의 피봇은 잠시 뒤에 맞춘다.
    bpy.app.timers.register(_default_pivot_timer, first_interval=0.1)
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
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    _restore_conflicts()
    _restore_zoom_direction()
    _unregister_keymaps()
    # 활성 키맵에 남은 애드온 항목 사본을 바로 걷어낸다.
    if bpy.context.window_manager.keyconfigs.user is not None:
        bpy.context.window_manager.keyconfigs.update()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
