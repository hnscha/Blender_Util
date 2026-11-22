# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import BoolProperty

from ..ops.hotkey_ops import BUTTONS
from .icons import icon_id
from .panels import _grid, button_available, draw_button, shortcut_info

ADDON_ID = __package__.rsplit('.', 1)[0]
GROUPS = (('KEY', '키'), ('TRANSFORM', '변환'), ('SELECT', '선택'),
          ('DISPLAY', '표시'), ('VIEW', '보기'))
PIES = (('ANIMSTUDIO_MT_key_pie', 'Key Pie'), ('ANIMSTUDIO_MT_view_pie', 'View Pie'))
_addon_keymaps = []


class ANIMSTUDIO_preferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    show_header: BoolProperty(name='Header', description='3D 뷰 헤더에 도구 묶음과 자주 쓰는 버튼 표시',
                              default=True)

    def draw(self, _context):
        self.layout.prop(self, 'show_header')


class Popover:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 14

    def draw(self, context):
        _grid(self.layout, context, self.group,
              shortcuts=shortcut_info(context, BUTTONS[self.group]))


_popovers = tuple(type('ANIMSTUDIO_PT_pop_' + group.lower(), (Popover, bpy.types.Panel), {
    '__module__': __name__, 'bl_idname': 'ANIMSTUDIO_PT_pop_' + group.lower(),
    'bl_label': label, 'group': group,
}) for group, label in GROUPS)


def draw_header(self, context):
    addon = context.preferences.addons.get(ADDON_ID)
    if addon is not None and not addon.preferences.show_header:
        return
    row = self.layout.row(align=True)
    for group, _label in GROUPS:
        row.popover(panel='ANIMSTUDIO_PT_pop_' + group.lower(), text='',
                    icon_value=icon_id(BUTTONS[group][0].icon, context))
    row = self.layout.row(align=True)
    for button in (BUTTONS['KEY'][0], BUTTONS['KEY'][2], BUTTONS['KEY'][3], BUTTONS['SELECT'][0]):
        if button_available(context, button):
            draw_button(row.row(align=True), context, button, icon_only=True)


class Pie:
    def draw(self, context):
        pie = self.layout.menu_pie()
        for index in self.indices:
            button = BUTTONS[self.group][index]
            if button_available(context, button):
                draw_button(pie.row(), context, button)
            else:
                # 방향은 선택 상태가 달라져도 고정한다.
                pie.separator()


class ANIMSTUDIO_MT_key_pie(Pie, bpy.types.Menu):
    bl_idname = 'ANIMSTUDIO_MT_key_pie'
    bl_label = '키'
    group = 'KEY'
    indices = (0, 1, 2, 3, 4, 5)


class ANIMSTUDIO_MT_view_pie(Pie, bpy.types.Menu):
    bl_idname = 'ANIMSTUDIO_MT_view_pie'
    bl_label = '보기'
    group = 'VIEW'
    indices = (0, 1, 2, 3, 4, 6)


_CLASSES = (ANIMSTUDIO_preferences, *_popovers, ANIMSTUDIO_MT_key_pie, ANIMSTUDIO_MT_view_pie)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_HT_tool_header.append(draw_header)
    config = bpy.context.window_manager.keyconfigs.addon
    if config is not None:
        km = config.keymaps.new(name='3D View', space_type='VIEW_3D')
        for name, _label in PIES:
            item = km.keymap_items.new('wm.call_menu_pie', 'NONE', 'PRESS')
            item.properties.name = name
            _addon_keymaps.append((km, item))


def unregister():
    for km, item in reversed(_addon_keymaps):
        km.keymap_items.remove(item)
    _addon_keymaps.clear()
    bpy.types.VIEW3D_HT_tool_header.remove(draw_header)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
