# SPDX-License-Identifier: GPL-3.0-or-later
"""
Maya/Unreal 식 뷰포트 조작 애드온.

애드온 키맵에만 항목을 추가한다 (Blender 기본 키맵/유저 키맵은 건드리지 않음).
애드온을 끄면 추가한 항목이 모두 제거된다.

  Q / W / E / R      선택(박스) / 이동 / 회전 / 스케일 툴
  Alt + 왼쪽 드래그   뷰 회전 (Orbit)
  Alt + 가운데 드래그 뷰 이동 (Pan)
  Alt + 오른쪽 드래그 뷰 줌 (Dolly)
"""

import bpy

# QWER 툴 전환을 넣을 모드별 키맵. 모드 키맵이 3D View/Window 키맵보다 먼저 처리되므로
# 기본 키(E=Extrude, R=Rotate 등)보다 우선한다.
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

# (keymap, keymap_item) 목록. unregister 에서 그대로 제거한다.
_addon_keymaps = []


class MayaUnrealNavPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        layout = self.layout
        inputs = context.preferences.inputs

        col = layout.column(align=True)
        col.label(text="Q / W / E / R : 선택 / 이동 / 회전 / 스케일")
        col.label(text="Alt + 왼쪽 / 가운데 / 오른쪽 드래그 : 회전 / 이동 / 줌")

        if inputs.use_mouse_emulate_3_button:
            box = layout.box()
            box.alert = True
            box.label(text="Emulate 3 Button Mouse 가 켜져 있어 Alt+왼쪽 드래그가 겹칩니다.", icon='ERROR')
            box.prop(inputs, "use_mouse_emulate_3_button")

        box = layout.box()
        box.label(text="Blender 내비게이션 설정 (선택)")
        box.prop(inputs, "use_zoom_to_mouse")
        box.prop(inputs, "use_rotate_around_active")


def _register_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        # 백그라운드 모드에는 애드온 키컨피그가 없다.
        return

    km = kc.keymaps.new(name="3D View", space_type='VIEW_3D')
    for idname, mouse in _NAV_KEYS:
        kmi = km.keymap_items.new(idname, mouse, 'PRESS', alt=True)
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


def register():
    bpy.utils.register_class(MayaUnrealNavPreferences)
    _register_keymaps()


def unregister():
    _unregister_keymaps()
    bpy.utils.unregister_class(MayaUnrealNavPreferences)
