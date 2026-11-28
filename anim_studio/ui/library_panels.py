# SPDX-License-Identifier: GPL-3.0-or-later
import bpy

from ..core import pose_library
from ..core.context import active_armature
from . import picker
from .pose_panels import ToolPanel


class ANIMSTUDIO_UL_assets(bpy.types.UIList):
    def filter_items(self, context, data, propname):
        return [self.bitflag_filter_item if pose_library.is_pose_asset(a) and
                (not self.filter_name or self.filter_name.casefold() in a.name.casefold()) else 0
                for a in getattr(data, propname)], []

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        if item.preview and item.preview.icon_id:
            row.template_icon(icon_value=item.preview.icon_id, scale=2.4)
        else:
            row.label(text='', icon='ASSET_MANAGER')
        col = row.column(align=True)
        col.label(text=item.name, translate=False)
        if item.asset_data:
            col.label(text=item.asset_data.catalog_simple_name or 'Unassigned', translate=False)


class ANIMSTUDIO_PT_picker(ToolPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_picker'
    bl_label = '본 피커'
    tab, asset = 'POSE', 'select_com'

    @classmethod
    def poll(cls, context):
        return context.window_manager.anim_studio_tab == 'POSE' and active_armature(context) is not None

    def draw(self, context):
        layout = self.options()
        row = layout.row(align=True)
        row.operator_context = 'INVOKE_REGION_WIN'
        row.operator('anim_studio.picker', text='Picker', depress=context.area.as_pointer() in picker.sessions)
        row.operator('anim_studio.picker_rebuild', text='Rebuild Layout')


class ANIMSTUDIO_PT_library(ToolPanel, bpy.types.Panel):
    bl_idname = 'ANIMSTUDIO_PT_library'
    bl_label = '포즈 라이브러리'
    tab, asset = 'POSE', 'pose_library'

    def draw(self, context):
        layout, wm = self.options(), context.window_manager
        layout.operator_context = 'INVOKE_REGION_WIN'
        row = layout.row(align=True)
        row.operator('anim_studio.asset_save', text='Save Pose', icon='ADD')
        row.operator('anim_studio.asset_shelf', text='Asset Shelf', icon='ASSET_MANAGER')
        layout.template_list('ANIMSTUDIO_UL_assets', '', bpy.data, 'actions', wm, 'anim_studio_asset_index', rows=3)
        row = layout.row(align=True)
        row.operator('anim_studio.asset_apply', text='Apply').flipped = False
        row.operator('anim_studio.asset_apply', text='Apply Mirror').flipped = True
        row = layout.row(align=True)
        row.operator('anim_studio.asset_blend', text='Blend…', icon='IPO_LINEAR')
        row.operator('anim_studio.asset_apply', text='50%').factor = .5
        row = layout.row(align=True)
        row.prop(wm, 'anim_studio_asset_selected', text='Selected', toggle=True)
        row.prop(wm, 'anim_studio_asset_key', toggle=True)
        layout.prop(wm, 'anim_studio_asset_mode')


_CLASSES = (ANIMSTUDIO_UL_assets, ANIMSTUDIO_PT_picker, ANIMSTUDIO_PT_library)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    picker.cleanup()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
