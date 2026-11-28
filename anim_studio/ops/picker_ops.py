# SPDX-License-Identifier: GPL-3.0-or-later
import json

import bpy
from bpy.props import EnumProperty, StringProperty

from ..core import picker
from ..core.context import active_armature
from ..core.errors import StudioError
from .hotkey_ops import StudioOperator, set_result


class ANIMSTUDIO_OT_picker_rebuild(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.picker_rebuild'
    bl_label = 'Rebuild Layout'
    bl_description = 'Rebuild Layout\n아마추어 데이터에 피커 배치를 다시 저장합니다'

    def run(self, context):
        arm = active_armature(context)
        if arm is None:
            raise StudioError('아마추어를 선택하세요.')
        return f'{len(picker.rebuild(arm))} controls'


class ANIMSTUDIO_OT_picker_select(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.picker_select'
    bl_label = 'Picker Select'
    bl_description = 'Picker Select\n현재 모드에서 보이는 컨트롤을 선택합니다'
    names: StringProperty(default='[]', options={'SKIP_SAVE'})
    operation: EnumProperty(items=[(n, n.title(), '') for n in ('SET', 'ADD', 'SUB', 'TOGGLE')])

    def run(self, context):
        arm = active_armature(context)
        if arm is None or not arm.is_editable:
            raise StudioError('편집할 수 있는 아마추어를 선택하세요.')
        if context.mode != 'POSE':
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.object.mode_set(mode='POSE')
        result = picker.select(arm, json.loads(self.names), self.operation)
        return f'{len(result)} selected'


class ANIMSTUDIO_OT_picker(StudioOperator, bpy.types.Operator):
    bl_idname = 'anim_studio.picker'
    bl_label = 'Picker'
    bl_description = 'Picker\n떠 있는 본 피커를 켜거나 끕니다. 제목 이동 · 오른쪽 아래 크기'
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        from ..ui.picker import PickerSession, sessions
        key = context.area.as_pointer()
        if key in sessions:
            sessions[key].close()
            return {'FINISHED'}
        if active_armature(context) is None:
            self.report({'ERROR'}, '아마추어를 선택하세요.')
            return {'CANCELLED'}
        self._session = PickerSession(context)
        sessions[key] = self._session
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def _select(self, context, names, operation):
        return bpy.ops.anim_studio.picker_select('EXEC_DEFAULT', True, names=json.dumps(names), operation=operation)

    def modal(self, context, event):
        from ..ui.picker import contains
        s = self._session
        if s.closed:
            return {'CANCELLED'}
        window, area = s.surface()
        if area is None:
            s.close()
            return {'CANCELLED'}
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if region is None:
            s.close()
            return {'CANCELLED'}
        try:
            with context.temp_override(window=window, area=area, region=region):
                scale, rect, body, chips = s.metrics(context)
                mx, my = event.mouse_x - region.x, event.mouse_y - region.y
                inside = contains(rect, mx, my)
                if not inside and not s.drag:
                    # 피커 밖 이벤트는 배치를 읽지 않고 그대로 넘긴다.
                    if s.hover:
                        s.hover = ''
                        window.workspace.status_text_set(None)
                        area.tag_redraw()
                    return {'PASS_THROUGH'}
                arm = active_armature(context)
                try:
                    buttons = picker.layout(arm) if arm else []
                except StudioError:
                    buttons = []  # 그리기 쪽이 Rebuild 안내를 띄운다. 피커는 유지.
                enabled = picker.enabled(arm) if arm else set()
                hit = next((b for b in buttons if contains(s.button_rect(b, body), mx, my)), None)
                if event.type == 'MOUSEMOVE':
                    if s.drag:
                        kind, ox, oy, original, operation = s.drag
                        dx, dy = (mx - ox) / scale, (my - oy) / scale
                        if kind == 'MOVE':
                            s.rect[:2] = [original[0] + dx, original[1] + dy]
                        elif kind == 'RESIZE':
                            s.rect[2:] = [max(220, original[2] + dx), max(300, original[3] - dy)]
                            s.rect[1] = original[1] + original[3] - s.rect[3]
                        else:
                            s.box = (ox, oy, mx, my)
                        area.tag_redraw()
                        return {'RUNNING_MODAL'}
                    hover = hit['bone'] if hit else ''
                    if hover != s.hover:
                        s.hover = hover
                        window.workspace.status_text_set(hover or None)
                    return {'RUNNING_MODAL'} if inside else {'PASS_THROUGH'}
                if event.type in {'ESC', 'RIGHTMOUSE', 'WINDOW_DEACTIVATE'} and s.drag:
                    s.rect[:] = s.drag[3]
                    s.drag = s.box = None
                    area.tag_redraw()
                    return {'RUNNING_MODAL'}
                if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and s.drag:
                    kind, ox, oy, original, operation = s.drag
                    if kind == 'BOX' and arm:
                        names = picker.box_names(buttons, ((ox - body[0]) / body[2], (oy - body[1]) / body[3],
                                                          (mx - body[0]) / body[2], (my - body[1]) / body[3])) if abs(mx - ox) + abs(my - oy) > 4 else []
                        self._select(context, names, operation)
                    s.drag = s.box = None
                    area.tag_redraw()
                    return {'RUNNING_MODAL'}
                if not inside:
                    return {'PASS_THROUGH'}
                if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                    operation = 'SUB' if event.ctrl else 'ADD' if event.shift else 'SET'
                    x, y, w, h = rect
                    if my > y + h - 32 * scale:
                        if mx > x + w - 32 * scale:
                            s.close()
                            return {'FINISHED'}
                        s.drag = ('MOVE', mx, my, s.rect[:], operation)
                    elif mx > x + w - 22 * scale and my < y + 22 * scale:
                        s.drag = ('RESIZE', mx, my, s.rect[:], operation)
                    elif arm:
                        chip = next((c for c, r in chips if contains(r, mx, my)), None)
                        if chip:
                            self._select(context, picker.chip_names(arm, buttons, chip), operation)
                        elif hit:
                            if hit['bone'] in enabled:
                                self._select(context, [hit['bone']], 'TOGGLE' if event.shift and not event.ctrl else operation)
                        elif contains(body, mx, my):
                            s.drag = ('BOX', mx, my, s.rect[:], operation)
                    area.tag_redraw()
                # 키보드 단축키는 피커 위에서도 통과한다. 포인터 이벤트만 가로챈다.
                return {'RUNNING_MODAL'} if event.type in {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} else {'PASS_THROUGH'}
        except StudioError as error:
            s.close()
            self.report({'ERROR'}, str(error))
            set_result(context, str(error), False)
            return {'CANCELLED'}
        except BaseException:
            s.close()
            raise

    def cancel(self, context):
        self._session.close()


_CLASSES = (ANIMSTUDIO_OT_picker_rebuild, ANIMSTUDIO_OT_picker_select, ANIMSTUDIO_OT_picker)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    from ..ui.picker import cleanup
    cleanup()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
