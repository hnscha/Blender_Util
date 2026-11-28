# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from ..core import pose_library as library
from ..core.context import active_armature
from ..core.errors import StudioError
from .hotkey_ops import StudioOperator, set_result
from .pose_ops import PoseOperator

_sessions = []
_catalog_items = []  # Blender 동적 enum 문자열의 수명을 유지한다.


def catalog_items(self, context):
    return _catalog_items or [('NONE', 'Unassigned', '')]


def selected_asset(context):
    action = bpy.data.actions.get(context.window_manager.anim_studio_asset_name)
    if action is None or not library.is_pose_asset(action):
        raise StudioError('현재 파일의 포즈 애셋을 선택하세요.')
    return action


def session(context, flipped=False):
    arm = active_armature(context)
    if arm is None or context.mode != 'POSE' or not arm.is_editable:
        raise StudioError('편집할 수 있는 아마추어의 포즈 모드에서 실행하세요.')
    wm = context.window_manager
    result, extras = library.target(arm, selected_asset(context), selected_only=wm.anim_studio_asset_selected,
                                    current_mode_only=wm.anim_studio_asset_mode, flipped=flipped)
    return library.BlendSession(arm, result, extras, wm.anim_studio_asset_mode)


def should_key(context):
    return context.window_manager.anim_studio_asset_key or context.scene.tool_settings.use_keyframe_insert_auto


class ANIMSTUDIO_OT_asset_save(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.asset_save'
    bl_label = 'Save Pose'
    bl_description = 'Save Pose\n선택 본을 현재 파일의 포즈 애셋으로 저장하고 현재 뷰를 썸네일로 사용합니다'
    pose_name: StringProperty(name='Name', default='Pose')
    catalog: EnumProperty(name='Catalog', items=catalog_items)
    catalog_path: StringProperty(name='New Catalog', description='새 카탈로그 경로. 비우면 선택한 카탈로그 사용')

    def invoke(self, context, event):
        _catalog_items[:] = [(key or 'NONE', value[0], value[1]) for key, value in library.catalogs().items()]
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, 'pose_name')
        self.layout.prop(self, 'catalog')
        self.layout.prop(self, 'catalog_path')

    def run(self, context):
        action = library.save(context, self.pose_name,
                              catalog_id='' if self.catalog_path or self.catalog == 'NONE' else self.catalog,
                              catalog_path=self.catalog_path)
        context.window_manager.anim_studio_asset_name = action.name
        return 'Saved · ' + action.name


class ANIMSTUDIO_OT_asset_apply(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.asset_apply'
    bl_label = 'Apply Pose'
    bl_description = 'Apply Pose\n선택 애셋의 로컬 자세를 현재 프레임에 적용합니다'
    flipped: BoolProperty(name='Mirror', default=False)
    factor: FloatProperty(name='Factor', default=1, min=0, soft_max=1, max=2)

    def run(self, context):
        return session(context, self.flipped).apply(self.factor, key=should_key(context))


class ANIMSTUDIO_OT_asset_shelf(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.asset_shelf'
    bl_label = 'Asset Shelf'
    bl_description = 'Asset Shelf\n3D 뷰 애셋 셸프를 엽니다'
    bl_options = {'REGISTER'}

    def run(self, context):
        context.space_data.show_region_asset_shelf = True
        return 'Asset Shelf'


class ANIMSTUDIO_OT_asset_blend(PoseOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.asset_blend'
    bl_label = 'Blend Pose'
    bl_description = 'Blend Pose\n슬라이더 드래그 후 놓기 확정 · Ctrl 오버슈트 · Esc 취소'
    factor: FloatProperty(name='Blend', default=0, min=0, soft_max=1, max=2)

    def run(self, context):
        return session(context).apply(self.factor, key=should_key(context))

    def invoke(self, context, event):
        try:
            self._session = session(context)
        except StudioError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self._closed = False
        self._area_id = context.area.as_pointer()
        self._window_id = context.window.as_pointer()
        self._start_x, self._start_factor = event.mouse_x, self.factor
        self._dragging = False
        self._key = should_key(context)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw, (), 'WINDOW', 'POST_PIXEL')
        _sessions.append(self)
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def _surface(self):
        for window in bpy.context.window_manager.windows:
            if window.as_pointer() == self._window_id:
                for area in window.screen.areas:
                    if area.as_pointer() == self._area_id and area.type == 'VIEW_3D':
                        return window, area
        return None, None

    def _rect(self, context):
        scale = context.preferences.system.ui_scale
        width = min(380 * scale, context.region.width - 40)
        return (context.region.width - width) / 2, 50 * scale, width, 68 * scale

    def _draw(self):
        import gpu
        from ..ui.picker import rounded, text
        context = bpy.context
        if self._closed or context.area.as_pointer() != self._area_id:
            return
        x, y, w, h = self._rect(context)
        scale = context.preferences.system.ui_scale
        previous = gpu.state.blend_get()
        gpu.state.blend_set('ALPHA')
        try:
            rounded((x, y, w, h), (.04, .07, .11, .97), 10 * scale)
            text(f'Blend · {self.factor * 100:.1f}%   |   Esc: Cancel', x + 12 * scale, y + h - 20 * scale, 12 * scale)
            rounded((x + 16 * scale, y + 20 * scale, w - 32 * scale, 5 * scale), (.20, .30, .40, 1))
            rounded((x + 16 * scale, y + 20 * scale, max(1, (w - 32 * scale) * min(1, self.factor)), 5 * scale), (.3, .7, .9, 1))
            rounded((x + 12 * scale + (w - 32 * scale) * min(1, self.factor), y + 15 * scale, 10 * scale, 15 * scale), (1, 1, 1, 1))
        finally:
            gpu.state.blend_set(previous)

    def finish(self, restore=False):
        if self._closed:
            return
        self._closed = True
        try:
            if restore:
                self._session.restore()
        finally:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
            if self in _sessions:
                _sessions.remove(self)
            window, area = self._surface()
            if area:
                area.tag_redraw()
            self._session = None

    def modal(self, context, event):
        if self._closed:
            return {'CANCELLED'}
        try:
            window, area = self._surface()
            if area is None or event.type in {'ESC', 'RIGHTMOUSE', 'WINDOW_DEACTIVATE'}:
                self.finish(restore=True)
                return {'CANCELLED'}
            if context.mode != 'POSE' or active_armature(context) is None or context.active_object.name != self._session.arm_name:
                raise StudioError('포즈 대상 또는 모드가 변경되어 블렌드를 취소했습니다.')
            if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS' or (
                    event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self._dragging):
                message = self._session.apply(self.factor, key=self._key)
                self.finish()
                set_result(context, message)
                return {'FINISHED'}
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                self._dragging = True
                self._start_x, self._start_factor = event.mouse_x, self.factor
                # 실제 표시한 슬라이더를 누르면 그 위치부터 시작한다.
                from ..ui.picker import contains
                region = next(r for r in area.regions if r.type == 'WINDOW')
                with context.temp_override(area=area, region=region):
                    rect = self._rect(context)
                    mx, my = event.mouse_x - region.x, event.mouse_y - region.y
                    if contains(rect, mx, my):
                        scale = context.preferences.system.ui_scale
                        self.factor = min(1, max(0, (mx - rect[0] - 16 * scale) / (rect[2] - 32 * scale)))
                        self._start_factor = self.factor
                        self._session.apply(self.factor)
            if event.type == 'MOUSEMOVE':
                self.factor = max(0, min(2 if event.ctrl else 1,
                                        self._start_factor + (event.mouse_x - self._start_x) / (300 * context.preferences.system.ui_scale)))
                self._session.apply(self.factor)
                area.tag_redraw()
            return {'RUNNING_MODAL'}
        except StudioError as error:
            self.finish(restore=True)
            self.report({'ERROR'}, str(error))
            set_result(context, str(error), False)
            return {'CANCELLED'}
        except BaseException:
            self.finish(restore=True)
            raise

    def cancel(self, context):
        self.finish(restore=True)


def cleanup():
    for operator in list(_sessions):
        operator.finish(restore=True)


_CLASSES = (ANIMSTUDIO_OT_asset_save, ANIMSTUDIO_OT_asset_apply, ANIMSTUDIO_OT_asset_shelf, ANIMSTUDIO_OT_asset_blend)
def _get_index(wm):
    # 목록 강조는 이름에서 계산한다. 액션이 새로 생겨 순서가 바뀌어도 선택이 밀리지 않는다.
    return bpy.data.actions.find(wm.anim_studio_asset_name)


def _set_index(wm, value):
    wm.anim_studio_asset_name = bpy.data.actions[value].name if 0 <= value < len(bpy.data.actions) else ''


_PROPS = ('anim_studio_asset_name', 'anim_studio_asset_index', 'anim_studio_asset_selected', 'anim_studio_asset_mode', 'anim_studio_asset_key')


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    wm = bpy.types.WindowManager
    wm.anim_studio_asset_name = StringProperty(name='Pose Asset', options={'SKIP_SAVE'})
    wm.anim_studio_asset_index = IntProperty(get=_get_index, set=_set_index, options={'SKIP_SAVE'})
    wm.anim_studio_asset_selected = BoolProperty(name='Selected Only', default=False, options={'SKIP_SAVE'})
    wm.anim_studio_asset_mode = BoolProperty(name='Current Mode Only', default=True, options={'SKIP_SAVE'})
    wm.anim_studio_asset_key = BoolProperty(name='Key', description='적용 후 키 기록. 오토키를 켜면 자동 기록', default=False, options={'SKIP_SAVE'})


def unregister():
    cleanup()
    _catalog_items.clear()
    for name in _PROPS:
        delattr(bpy.types.WindowManager, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
