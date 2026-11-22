# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from ..core.context import active_armature, is_rig_studio
from ..ops.hotkey_ops import BUTTONS, visibility_on
from .icons import icon_id

# 부모 draw가 매번 갱신하고 자식들이 공유한다. RNA 참조는 보관하지 않는다.
_draw_shortcuts = {}


def _matches(item, button):
    if item.idname != button.operator:
        return False
    if any(getattr(item.properties, key) != value for key, value in button.properties.items()):
        return False
    for key in item.properties.keys():
        if key in button.properties:
            continue
        prop = item.properties.bl_rna.properties[key]
        if prop.type in {'POINTER', 'COLLECTION'}:
            return False
        is_array = prop.type in {'BOOLEAN', 'INT', 'FLOAT'} and prop.is_array
        if prop.type == 'ENUM' and prop.is_enum_flag:
            default = prop.default_flag
        else:
            default = tuple(prop.default_array) if is_array else prop.default
        value = getattr(item.properties, key)
        if (tuple(value) if is_array else value) != default:
            return False
    return True


def shortcut_info(context, buttons):
    """사용자 키맵을 읽기만 한다. 그리는 동안 RNA/키맵을 갱신하지 않는다."""
    config = context.window_manager.keyconfigs.user
    if config is None:
        return {}
    by_operator = {}
    for km in config.keymaps:
        for item in km.keymap_items:
            if (item.active and item.type != 'NONE'
                    and (item.is_user_defined or item.is_user_modified)):
                by_operator.setdefault(item.idname, []).append(item)
    studio_items = [item for name, items in by_operator.items()
                    if name.startswith('anim_studio.') for item in items]
    result = {}
    for button in buttons:
        assigned = [item for item in by_operator.get(button.operator, ()) if _matches(item, button)]
        keys, conflicts = [], []
        for item in assigned:
            label = item.to_string(compact=True)
            if label not in keys:
                keys.append(label)
            for other in studio_items if button.operator.startswith('anim_studio.') else ():
                if other == item or _matches(other, button):
                    continue
                if item.compare(other) and other.name not in conflicts:
                    conflicts.append(other.name)
        result[button.label] = (" / ".join(keys), conflicts)
    return result


def button_available(context, button):
    if button.keymaps == ('Pose',) and context.mode != 'POSE':
        return False
    if button.keymaps == ('Object Mode',) and context.mode != 'OBJECT':
        return False
    if button.operator in {'anim_studio.toggle_safe_areas', 'anim_studio.toggle_thirds'}:
        if context.scene.camera is None or context.scene.camera.type != 'CAMERA':
            return False
    module, name = button.operator.split('.')
    return getattr(getattr(bpy.ops, module), name).poll()


def draw_button(layout, context, button, *, icon_only=False):
    layout.operator_context = 'INVOKE_REGION_WIN'
    layout.alert = button.alert
    if button.primary and not icon_only:
        layout.scale_y = 1.3
    props = layout.operator(button.operator, text='' if icon_only else button.short,
                            icon_value=icon_id(button.icon, context), translate=False)
    for name, value in button.properties.items():
        setattr(props, name, value)
    return props


def _grid(layout, context, group, *, shortcuts=None):
    layout.use_property_split = True
    layout.use_property_decorate = False
    buttons = [button for button in BUTTONS[group] if button_available(context, button)]
    if shortcuts is None:
        shortcuts = _draw_shortcuts.get(context.region.as_pointer(), {})
    grid = layout.grid_flow(row_major=True, columns=0, even_columns=True, align=True)
    grid.operator_context = 'INVOKE_REGION_WIN'
    for button in buttons:
        row = grid.row(align=True)
        draw_button(row, context, button)
        if group == 'DISPLAY':
            row.label(text='', icon='CHECKMARK' if visibility_on(
                context.space_data, button.properties['kind']) else 'BLANK1')
        key, conflicts = shortcuts.get(button.label, ('', []))
        if key:
            key_row = row.row(align=True)
            key_row.scale_x = 0.8
            key_row.label(text=key, icon='ERROR' if conflicts else 'NONE', translate=False)


class ANIMSTUDIO_PT_main(bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_main'
    bl_label = '애니메이션'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Anim Studio'

    def draw_header(self, context):
        self.layout.label(text='', icon_value=icon_id('anim_studio', context))

    def draw(self, context):
        layout = self.layout
        row = layout.box().row(align=True)
        arm = active_armature(context)
        if arm is None:
            row.label(text='아마추어 선택', icon='INFO')
        else:
            row.label(text=arm.name, icon='ARMATURE_DATA', translate=False)
            badge = row.row()
            badge.alignment = 'RIGHT'
            rig = is_rig_studio(arm)
            badge.label(text='Rig Studio' if rig else 'Generic',
                        icon='CHECKMARK' if rig else 'BONE_DATA', translate=False)
        layout.row(align=True).prop(context.window_manager, 'anim_studio_tab', expand=True,
                                    icon_only=context.region.width < 260)
        shortcuts = shortcut_info(context, [b for group in BUTTONS.values() for b in group])
        _draw_shortcuts.clear()
        _draw_shortcuts[context.region.as_pointer()] = shortcuts
        conflicts = list(dict.fromkeys(name for _key, names in shortcuts.values() for name in names))
        wm = context.window_manager
        if conflicts:
            layout.row().label(text='키 겹침 · ' + ', '.join(conflicts), icon='ERROR', translate=False)
        if wm.anim_studio_last:
            layout.row().label(text=wm.anim_studio_last,
                               icon='CHECKMARK' if wm.anim_studio_last_ok else 'ERROR', translate=False)
        layout.operator('anim_studio.open_keymap', text='Keymap…', icon_value=icon_id('keymap', context), translate=False)
        row = layout.row(align=True)
        row.operator_context = 'INVOKE_REGION_WIN'
        row.operator('wm.call_menu_pie', text='Key Pie', icon_value=icon_id('key_all', context),
                     translate=False).name = 'ANIMSTUDIO_MT_key_pie'
        row.operator('wm.call_menu_pie', text='View Pie', icon_value=icon_id('view_front_back', context),
                     translate=False).name = 'ANIMSTUDIO_MT_view_pie'


class HotkeyPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Anim Studio'
    bl_parent_id = 'ANIMSTUDIO_PT_main'

    @classmethod
    def poll(cls, context):
        return context.window_manager.anim_studio_tab == 'HOTKEY'

    def draw_header(self, context):
        self.layout.label(text='', icon_value=icon_id(BUTTONS[self.group][0].icon, context))

    def draw(self, context):
        _grid(self.layout, context, self.group)


class ANIMSTUDIO_PT_keys(HotkeyPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_keys'
    bl_label = '키'
    bl_order = 0
    group = 'KEY'


class ANIMSTUDIO_PT_transform(HotkeyPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_transform'
    bl_label = '변환'
    bl_order = 1
    group = 'TRANSFORM'


class ANIMSTUDIO_PT_select(HotkeyPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_select'
    bl_label = '선택'
    bl_order = 2
    group = 'SELECT'


class ANIMSTUDIO_PT_display(HotkeyPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_display'
    bl_label = '표시'
    bl_order = 3
    group = 'DISPLAY'


class ANIMSTUDIO_PT_view(HotkeyPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_view'
    bl_label = '보기'
    bl_order = 4
    group = 'VIEW'

_CLASSES = (ANIMSTUDIO_PT_main, ANIMSTUDIO_PT_keys, ANIMSTUDIO_PT_transform,
            ANIMSTUDIO_PT_select, ANIMSTUDIO_PT_display, ANIMSTUDIO_PT_view)


def register():
    bpy.types.WindowManager.anim_studio_tab = EnumProperty(
        name='Group', description='도구 묶음',
        items=(('POSE', '포즈', '포즈 복사·미러·트윈', 'ARMATURE_DATA', 0),
               ('KEY', '키', 'Key Match·키 정리', 'KEYFRAME', 1),
               ('SPACE', '공간', '임시 피봇·월드 고정·정렬', 'ORIENTATION_GLOBAL', 2),
               ('HOTKEY', '단축키', '단축키 지정용 버튼 모음', 'EVENT_K', 3)), default='HOTKEY')
    bpy.types.WindowManager.anim_studio_last = StringProperty(
        name='Result', description='마지막 실행 결과', options={'SKIP_SAVE'})
    bpy.types.WindowManager.anim_studio_last_ok = BoolProperty(
        name='Success', description='마지막 실행의 성공 여부', default=True, options={'SKIP_SAVE'})
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    _draw_shortcuts.clear()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.anim_studio_last_ok
    del bpy.types.WindowManager.anim_studio_last
    del bpy.types.WindowManager.anim_studio_tab
