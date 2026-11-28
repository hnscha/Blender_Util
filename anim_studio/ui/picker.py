# SPDX-License-Identifier: GPL-3.0-or-later
"""이벤트가 올 때만 동작하는 피커. draw에서는 RNA를 읽기만 한다."""
import json
import math

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..core import picker
from ..core.context import active_armature
from ..core.errors import StudioError

sessions = {}
geometry = {}
COLORS = {'L': (.17, .42, .80, 1), 'R': (.48, .72, .20, 1), 'C': (.10, .57, .53, 1),
          'COM': (.82, .66, .16, 1), 'HEAD': (.80, .36, .55, 1), 'FINGER': (.32, .58, .64, 1)}


def rounded(rect, color, radius=5):
    x, y, w, h = rect
    radius = max(0, min(radius, w / 2, h / 2))
    perimeter = []
    for cx, cy, start in ((x + w - radius, y + h - radius, 0), (x + radius, y + h - radius, 90),
                          (x + radius, y + radius, 180), (x + w - radius, y + radius, 270)):
        for i in range(7):
            angle = math.radians(start + i * 15)
            perimeter.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    center = (x + w / 2, y + h / 2)
    vertices = [p for i, point in enumerate(perimeter) for p in (center, point, perimeter[(i + 1) % len(perimeter)])]
    gpu.state.blend_set('ALPHA')  # blf 그리기가 혼합 상태를 바꿀 수 있다.
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {'pos': vertices})
    shader.bind()
    shader.uniform_float('color', color)
    batch.draw(shader)


def polygon(points, color):
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    center = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
    vertices = [v for i, p in enumerate(points) for v in (center, p, points[(i + 1) % len(points)])]
    batch = batch_for_shader(shader, 'TRIS', {'pos': vertices})
    shader.bind()
    shader.uniform_float('color', color)
    batch.draw(shader)


def draw_shape(shape, rect, color, scale=1.0):
    x, y, w, h = rect
    if shape == 'DIAMOND':
        polygon([(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)], color)
    elif shape in {'CIRCLE', 'BAR'}:
        rounded(rect, color, min(w, h) / 2)
    else:
        rounded(rect, color, min(7 * scale, w / 2, h / 2))


def text(label, x, y, size, color=(.92, .95, 1, 1), width=None):
    blf.size(0, size)
    if width is not None:
        while label and blf.dimensions(0, label)[0] > width:
            label = label[:-1]
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, label)


def contains(rect, x, y):
    return rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3]


class PickerSession:
    def __init__(self, context):
        self.area_id = context.area.as_pointer()
        self.window_id = context.window.as_pointer()
        self.rect = list(geometry.get(self.area_id, (16, 40, 400, 560)))
        self.closed = False
        self.drag = None
        self.box = None
        self.hover = ''
        self.handle = bpy.types.SpaceView3D.draw_handler_add(self.draw, (), 'WINDOW', 'POST_PIXEL')

    def surface(self):
        for window in bpy.context.window_manager.windows:
            if window.as_pointer() == self.window_id:
                for area in window.screen.areas:
                    if area.as_pointer() == self.area_id and area.type == 'VIEW_3D':
                        return window, area
        return None, None

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self.handle, 'WINDOW')
            self.handle = None
        geometry[self.area_id] = tuple(self.rect)
        window, area = self.surface()
        if window:
            window.workspace.status_text_set(None)
        if area:
            area.tag_redraw()
        if sessions.get(self.area_id) is self:
            sessions.pop(self.area_id)
        self.drag = self.box = None

    def metrics(self, context):
        scale = context.preferences.system.ui_scale
        # 겹쳐 그려지는 툴바·사이드바·헤더 영역은 피한다.
        left = right = top = 0
        if context.preferences.system.use_region_overlap and context.area is not None:
            for other in context.area.regions:
                if other.type == 'TOOLS' and other.width > 1:
                    left = other.width
                elif other.type == 'UI' and other.width > 1:
                    right = other.width
                elif other.type in {'HEADER', 'TOOL_HEADER'} and other.height > 1:
                    top += other.height
        inner_w = context.region.width - left - right
        inner_h = context.region.height - top
        x, y, w, h = [v * scale for v in self.rect]
        w, h = max(1, min(w, inner_w - 8)), max(1, min(h, inner_h - 8))
        x = max(left + 4, min(left + x, left + inner_w - w - 4))
        y = max(4, min(y, inner_h - h - 4))
        # 몸 영역은 템플릿 비율을 유지해 가운데 맞춘다(늘이지 않음).
        bx, by = x + 10 * scale, y + 38 * scale
        bw, bh = max(1, w - 20 * scale), max(1, h - 80 * scale)
        if bw / bh > picker.ASPECT:
            bx, bw = bx + (bw - bh * picker.ASPECT) / 2, bh * picker.ASPECT
        else:
            by, bh = by + (bh - bw / picker.ASPECT) / 2, bw / picker.ASPECT
        body = (bx, by, bw, bh)
        count = len(picker.CHIPS)
        chip_w = (w - 16 * scale) / count
        chips = [(chip, (x + 8 * scale + i * chip_w + 1, y + 8 * scale, chip_w - 2, 20 * scale))
                 for i, chip in enumerate(picker.CHIPS)]
        return scale, (x, y, w, h), body, chips

    @staticmethod
    def button_rect(button, body):
        x, y, w, h = body
        return x + button['x'] * w, y + button['y'] * h, button['w'] * w, button['h'] * h

    def draw(self):
        context = bpy.context
        if self.closed or context.area is None or context.area.as_pointer() != self.area_id:
            return
        # 사분할 뷰에서도 이벤트를 처리하는 첫 WINDOW region 하나에만 그린다.
        first = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
        if first is None or context.region is None or context.region.as_pointer() != first.as_pointer():
            return
        scale, rect, body, chips = self.metrics(context)
        x, y, w, h = rect
        previous = gpu.state.blend_get()
        gpu.state.blend_set('ALPHA')
        try:
            rounded(rect, (.035, .047, .064, .96), 12 * scale)
            rounded((x, y + h - 32 * scale, w, 32 * scale), (.09, .13, .19, 1), 9 * scale)
            text('Anim Studio · Picker', x + 12 * scale, y + h - 22 * scale, 13 * scale)
            text('×', x + w - 24 * scale, y + h - 23 * scale, 18 * scale)
            text('◢', x + w - 14 * scale, y + 3 * scale, 12 * scale)
            arm = active_armature(context)
            if arm is None:
                text('Select an armature', body[0], body[1] + body[3] / 2, 13 * scale)
                return
            try:
                buttons = picker.layout(arm)
            except StudioError:
                text('Rebuild Layout', body[0], body[1] + body[3] / 2, 13 * scale)
                return
            enabled = picker.enabled(arm)
            for sx, sy, sw, sh, sr in picker.silhouette():
                r = self.button_rect({'x': sx, 'y': sy, 'w': sw, 'h': sh}, body)
                rounded(r, (.55, .65, .8, .06), min(r[2], r[3]) * sr)
            for b in buttons:
                pb = arm.pose.bones.get(b['bone'])
                r = self.button_rect(b, body)
                color = COLORS[b['group']]
                if b['group'] == 'FINGER':
                    color = COLORS['L' if b.get('zone') == 'Fingers L' else 'R']
                active = b['bone'] in enabled
                chosen = active and pb is not None and pb.select
                if not active:
                    color = tuple(c * .32 for c in color[:3]) + (.85,)
                elif chosen:
                    color = tuple(min(1, c * 1.25 + .18) for c in color[:3]) + (1,)
                elif b['bone'] == self.hover:
                    color = tuple(min(1, c * 1.12 + .06) for c in color[:3]) + (1,)
                shape = b.get('shape', 'BAR')
                if chosen:
                    pad = 2 * scale
                    draw_shape(shape, (r[0] - pad, r[1] - pad, r[2] + 2 * pad, r[3] + 2 * pad), (1, 1, 1, 1), scale)
                draw_shape(shape, r, color, scale)
                size = min(12 * scale, r[3] * .45)
                if b.get('label') and size >= 8 * scale:
                    ink = (.05, .07, .1, 1) if chosen else (1, 1, 1, .9)
                    blf.size(0, size)
                    tw = blf.dimensions(0, b['label'])[0]
                    text(b['label'], r[0] + (r[2] - tw) / 2, r[1] + r[3] / 2 - size * .35, size, ink)
            info = json.loads(arm.data.get('rig_studio_info', '{}'))
            tx, ty, tw_, th_ = picker.TEMPLATE
            for limb in info.get('limbs', []):
                pb = arm.pose.bones.get(limb['names']['ik'])
                if pb is None:
                    continue
                lx, ly = (66.0, 95.0) if limb['part'] == 'arm' else (61.0, 46.5)
                mode = 'IK' if pb['IK'] > .5 else 'FK'
                size = 10 * scale
                blf.size(0, size)
                width = blf.dimensions(0, mode)[0]
                px = body[0] + (lx - tx) / tw_ * body[2]
                if limb['side'] == 'R':
                    px = body[0] + (100 - lx - tx) / tw_ * body[2] - width
                text(mode, px, body[1] + (ly - ty) / th_ * body[3], size, (.75, .82, .9, .8))
            for chip, r in chips:
                rounded(r, (.13, .19, .26, 1), 5 * scale)
                label = picker.CHIP_LABELS.get(chip, chip)
                size = 10 * scale
                blf.size(0, size)
                tw = blf.dimensions(0, label)[0]
                if tw > r[2] - 4 * scale:
                    size = size * (r[2] - 4 * scale) / tw
                    blf.size(0, size)
                    tw = blf.dimensions(0, label)[0]
                text(label, r[0] + (r[2] - tw) / 2, r[1] + r[3] / 2 - size * .35, size)
            if self.box:
                ax, ay, bx, by = self.box
                rounded((min(ax, bx), min(ay, by), max(1, abs(ax - bx)), max(1, abs(ay - by))),
                        (.5, .75, 1, .25), 0)
        finally:
            gpu.state.blend_set(previous)


def cleanup():
    for session in list(sessions.values()):
        session.close()
    geometry.clear()
