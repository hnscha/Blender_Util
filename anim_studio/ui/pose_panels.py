# SPDX-License-Identifier: GPL-3.0-or-later
import json

import bpy

from ..ops.hotkey_ops import BUTTONS
from .icons import icon_id
from .panels import HotkeyPanel, button_available


def button(layout, context, operator, text=None, primary=False, **properties):
    definition = next(b for b in BUTTONS['POSE_TOOLS'] if b.operator == 'anim_studio.' + operator
                      and all(properties.get(k, v) == v for k, v in b.properties.items()))
    if not button_available(context, definition):
        return None
    row = layout.row(align=True)
    row.operator_context = 'INVOKE_REGION_WIN'
    if primary:
        row.scale_y = 1.3
    props = row.operator(definition.operator, text=definition.label if text is None else text,
                         icon_value=icon_id(definition.icon, context), translate=False)
    for name, value in {**definition.properties, **properties}.items():
        setattr(props, name, value)
    return props


class ToolPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Anim Studio'
    bl_parent_id = 'ANIMSTUDIO_PT_main'

    @classmethod
    def poll(cls, context):
        return (context.window_manager.anim_studio_tab == cls.tab
                and (cls.tab == 'SPACE' or context.mode == 'POSE'))

    def draw_header(self, context):
        self.layout.label(text='', icon_value=icon_id(self.asset, context))

    def options(self):
        self.layout.use_property_split = True
        self.layout.use_property_decorate = False
        return self.layout


class ANIMSTUDIO_UL_history(bpy.types.UIList):
    def draw_item(self, context, layout, _data, item, _icon, _active_data, _active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.label, translate=False)
        row.operator('anim_studio.pose_paste', text='', icon='PASTEDOWN').index = index
        row.operator('anim_studio.pose_history_delete', text='', icon='X').index = index


class ANIMSTUDIO_PT_copy(ToolPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_copy'
    bl_label = '포즈 복사'
    tab, asset = 'POSE', 'pose_library'

    def draw(self, context):
        layout, wm = self.options(), context.window_manager
        row = layout.row(align=True)
        button(row, context, 'pose_copy', 'Copy')
        button(row, context, 'pose_paste', 'Paste', primary=True)
        row = layout.row(align=True)
        button(row, context, 'pose_paste_opposite', 'Opposite')
        button(row, context, 'pose_paste_range', 'Range')
        layout.row(align=True).prop(wm, 'anim_studio_paste_space', expand=True)
        layout.template_list('ANIMSTUDIO_UL_history', '', wm, 'anim_studio_history', wm,
                             'anim_studio_history_index', rows=4)


class ANIMSTUDIO_PT_mirror(ToolPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_mirror'
    bl_label = '미러'
    tab, asset = 'POSE', 'select_opposite'

    def draw(self, context):
        layout = self.options()
        layout.row(align=True).prop(context.window_manager, 'anim_studio_mirror_mode', expand=True)
        button(layout, context, 'mirror_pose', 'Mirror Pose', primary=True)
        button(layout, context, 'mirror_range', 'Mirror Range')
        layout.separator()
        row = layout.row(align=True)
        button(row, context, 'mirror_loop', '1 → 2', half='FIRST')
        button(row, context, 'mirror_loop', '2 → 1', half='SECOND')
        button(row, context, 'mirror_loop_single', 'Frame')


class ANIMSTUDIO_PT_tween(ToolPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_tween'
    bl_label = '트윈'
    tab, asset = 'POSE', 'free_rotation'

    def draw(self, context):
        layout, wm = self.options(), context.window_manager
        target_row = layout.row(align=True)
        target_row.use_property_split = False
        target_row.prop(wm, 'anim_studio_tween_target', expand=True, icon_only=True)
        row = layout.row(align=True)
        row.operator_context = 'INVOKE_REGION_WIN'
        for factor in (0, 0.25, 0.5, 0.75, 1):
            props = row.operator('anim_studio.tween_set', text=str(round(factor * 100)), translate=False)
            props.target, props.factor = wm.anim_studio_tween_target, factor
        button(layout, context, 'tween', 'Tween…', primary=True, target=wm.anim_studio_tween_target)


class ANIMSTUDIO_PT_key_match(ToolPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_key_match'
    bl_label = '키 매칭'
    tab, asset = 'KEY', 'key_track'

    def draw(self, context):
        layout, wm = self.options(), context.window_manager
        row = layout.row(align=True)
        button(row, context, 'key_match', '◀ Prev', primary=True, direction='PREV')
        button(row, context, 'key_match', 'Next ▶', primary=True, direction='NEXT')
        row = layout.row(align=True)
        row.prop(wm, 'anim_studio_km_rot', toggle=True)
        row.prop(wm, 'anim_studio_km_world', toggle=True)


class ANIMSTUDIO_PT_pivot(ToolPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_pivot'
    bl_label = '임시 피봇'
    tab, asset = 'SPACE', 'vis_helpers'

    def draw(self, context):
        layout, wm = self.options(), context.window_manager
        row = layout.row(align=True)
        row.prop(wm, 'anim_studio_follow_pos', toggle=True)
        row.prop(wm, 'anim_studio_follow_rot', toggle=True)
        button(layout, context, 'temp_pivot_create', 'Create', primary=True)
        row = layout.row(align=True)
        button(row, context, 'temp_pivot_apply', 'Apply')
        button(row, context, 'temp_pivot_bake', 'Bake Range')
        button(row, context, 'temp_pivot_delete', 'Delete')
        pivots = [obj for obj in context.scene.objects if obj.get('anim_studio_pivot')]
        if len(pivots) == 1:
            pivot = pivots[0]
            arm = context.scene.objects.get(pivot.get('anim_studio_arm', ''))
            count = sum(any(c.type == 'CHILD_OF' and c.target == pivot for c in pb.constraints)
                        for pb in arm.pose.bones) if arm is not None and arm.type == 'ARMATURE' else 0
            layout.label(text=f'Target: {count} bones · Pivot: {pivot.name}', translate=False)
        else:
            layout.label(text='Pivot: (none)', translate=False)


class ANIMSTUDIO_PT_world(ToolPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_world'
    bl_label = '월드 고정'
    tab, asset = 'SPACE', 'toggle_world_local'

    def draw(self, context):
        layout, wm = self.options(), context.window_manager
        row = layout.row(align=True)
        row.use_property_split = False
        for prop in ('pos', 'rot', 'scale'):
            row.prop(wm, 'anim_studio_align_' + prop, toggle=True,
                     text={'pos': 'Pos', 'rot': 'Rot', 'scale': 'Scale'}[prop])
        button(layout, context, 'world_lock_range', 'Lock Range', primary=True)
        layout.separator()
        if wm.anim_studio_align:
            state = json.loads(wm.anim_studio_align)
            source = state['sources'][0] if len(state['sources']) == 1 else f'{len(state["sources"])} bones'
            layout.label(text=f'Source: {source} · Target: {state["target_bone"] or state["target_object"]}', translate=False)
        button(layout, context, 'align_set', 'Set from Selection')
        row = layout.row(align=True)
        button(row, context, 'align_now', 'Align Now')
        button(row, context, 'align_range', 'Align Range')


class ANIMSTUDIO_PT_pose_hotkeys(HotkeyPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_pose_hotkeys'
    bl_label = '포즈·키·공간'
    group = 'POSE_TOOLS'
    bl_order = 5


_CLASSES = (ANIMSTUDIO_UL_history, ANIMSTUDIO_PT_copy, ANIMSTUDIO_PT_mirror, ANIMSTUDIO_PT_tween,
            ANIMSTUDIO_PT_key_match, ANIMSTUDIO_PT_pivot, ANIMSTUDIO_PT_world, ANIMSTUDIO_PT_pose_hotkeys)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
