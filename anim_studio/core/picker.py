# SPDX-License-Identifier: GPL-3.0-or-later
"""리그 평가 없이 읽는 피커 배치·선택 엔진. 좌표 원점은 왼쪽 아래.

배치는 고정된 표준 사람 템플릿(정면, 살짝 벌린 차렷 자세)이다. 컨트롤은 리그 체형이
아니라 part·side·role·체인 순번으로 템플릿 자리에 매핑하므로 피커 모양은 항상 같다.
캐릭터 오른쪽 = 화면 왼쪽. 템플릿 단위는 가로 0~100, 세로 TEMPLATE 범위.
"""
import json
import math
import re
from collections import defaultdict

from .context import active_mode_controls, bone_meta, is_rig_studio
from .errors import StudioError
from . import mirror

PROPERTY = 'anim_studio_picker'
CHIPS = ('Arm R', 'Fingers R', 'Leg R', 'Spine', 'All', 'Mirror', 'Leg L', 'Fingers L', 'Arm L')
CHIP_LABELS = {'Fingers R': 'Hand R', 'Fingers L': 'Hand L'}
# x0, y0, width, height. 그리기는 이 비율을 유지한다(늘이지 않음).
TEMPLATE = (0.0, -9.0, 100.0, 127.0)
ASPECT = TEMPLATE[2] / TEMPLATE[3]
SHAPES = ('BAR', 'PLATE', 'DIAMOND', 'CIRCLE')
FINGERS = ('thumb', 'index', 'middle', 'ring', 'pinky')

# 캐릭터 왼쪽(화면 오른쪽) 기준 슬롯. 오른쪽은 x를 거울로 뒤집는다.
# (x, y, w, h, shape)
_LIMB = {
    'arm': {
        'clavicle': (60.5, 89.0, 7.0, 3.6, 'BAR'),
        'start': (68.5, 88.4, 4.6, 4.6, 'CIRCLE'),
        'up_fk': (68.0, 70.0, 5.6, 17.0, 'BAR'),
        'bend_up': (62.5, 76.5, 3.8, 3.8, 'DIAMOND'),
        'lo_tweak': (64.6, 66.0, 3.8, 3.8, 'CIRCLE'),
        'lo_fk': (70.6, 52.0, 5.4, 16.6, 'BAR'),
        'bend_lo': (65.8, 57.5, 3.8, 3.8, 'DIAMOND'),
        'pole': (78.6, 66.4, 4.4, 4.4, 'CIRCLE'),
        'end_fk': (72.4, 41.5, 7.4, 8.8, 'PLATE'),
        'ik': (81.6, 42.6, 6.6, 6.6, 'DIAMOND'),
    },
    'leg': {
        'start': (53.2, 45.4, 4.6, 4.6, 'CIRCLE'),
        'up_fk': (53.0, 27.5, 6.6, 17.0, 'BAR'),
        'bend_up': (61.0, 34.5, 3.8, 3.8, 'DIAMOND'),
        'lo_tweak': (61.0, 21.5, 3.8, 3.8, 'CIRCLE'),
        'lo_fk': (53.6, 10.5, 6.0, 16.0, 'BAR'),
        'bend_lo': (61.0, 14.0, 3.8, 3.8, 'DIAMOND'),
        'pole': (66.6, 25.8, 4.4, 4.4, 'CIRCLE'),
        'end_fk': (52.6, 3.4, 9.0, 5.8, 'PLATE'),
        'toe': (62.4, 3.4, 4.8, 4.2, 'BAR'),
        'ik': (69.0, 3.0, 6.6, 6.6, 'DIAMOND'),
    },
}
_CENTER = {
    'head': (42.0, 100.0, 16.0, 16.0, 'PLATE'),
    'neck': (47.6, 94.4, 4.8, 4.8, 'BAR'),
    'neck_ik': (54.0, 94.6, 4.4, 4.4, 'DIAMOND'),
    'pelvis': (42.0, 55.4, 16.0, 6.6, 'PLATE'),
    'pelvis_ik': (59.4, 56.4, 4.4, 4.4, 'DIAMOND'),
    'com': (38.0, 50.6, 24.0, 3.6, 'PLATE'),
    'root': (33.0, -0.6, 34.0, 3.0, 'PLATE'),
}
_TORSO = (41.4, 64.0, 13.6, 27.0)   # 척추 FK 판 기둥
_TORSO_IK = (56.0, 4.0)             # IK 다이아몬드 기둥 x, 크기
_HAND = (83.0, 7.0, 16.0, 30.0)     # 확대 손(캐릭터 왼쪽) 블록
_OTHER = (2.0, -8.6, 96.0, 6.4)

# 옅은 몸 실루엣. (x, y, w, h, radius 비율). 왼쪽 팔다리는 오른쪽으로 거울.
SILHOUETTE = (
    (41.0, 99.0, 18.0, 18.6, .5), (46.6, 93.0, 6.8, 7.0, .3), (39.4, 62.4, 21.2, 31.0, .25),
    (40.0, 53.6, 20.0, 9.6, .3),
    (59.6, 86.0, 15.0, 7.0, .45), (67.4, 69.0, 7.6, 20.0, .45), (69.8, 51.0, 7.2, 19.0, .45),
    (71.6, 40.6, 9.0, 11.0, .35),
    (52.2, 26.6, 8.4, 25.0, .4), (52.8, 9.6, 7.6, 18.0, .4), (51.8, 2.6, 15.8, 7.4, .4),
    (82.2, 5.6, 17.6, 32.6, .2),
)


def _mirror_x(slot):
    x, y, w, h, shape = slot
    return 100.0 - x - w, y, w, h, shape


def _number(name):
    match = re.search(r'(\d+)(?!.*\d)', name)
    return int(match.group(1)) if match else 0


def _side(pb):
    side = mirror._side(pb.id_data, pb.name)
    return side if side in {'L', 'R'} else 'C'


def _word(name, words):
    """대소문자 무시, 단어 경계(구분자·숫자·대문자 시작)로만 찾는다. 'earring'의 ring 제외."""
    for match in re.finditer('|'.join(words), name, re.IGNORECASE):
        start, end = match.span()
        before = start == 0 or not name[start - 1].isalpha() or name[start].isupper()
        after = end == len(name) or not name[end].islower()
        if before and after:
            return match.group().lower()
    return None


def _finger(name):
    found = _word(name, ('thumb', 'index', 'middle', 'ring', 'pinky', 'little'))
    return 'pinky' if found == 'little' else found


def classify(pb, rig=False):
    """(zone, group, part, role). role은 슬롯 이름(템플릿 키)."""
    meta = (bone_meta(pb) or {}) if rig else {}
    name = pb.name.lower()
    side = meta.get('side') if meta.get('side') in {'L', 'R'} else _side(pb)
    if rig:
        part, role = meta.get('part', ''), meta.get('role', '')
        if role == 'bend':
            role = 'bend_up' if re.search(r'upper|thigh', name) else 'bend_lo'
        if 'toe' in name:
            part, role = 'leg', 'toe'
        elif role == 'finger':
            part = 'finger'
        elif part == 'torso' and role in {'head', 'neck', 'neck_ik', 'pelvis', 'pelvis_ik', 'com', 'root'}:
            part = 'center'
        elif part == 'torso' or role.startswith('spine'):
            part, role = 'spine', ('spine_ik' if 'ik' in role else 'spine_fk')
        elif role in {'head', 'neck', 'neck_ik', 'pelvis', 'pelvis_ik', 'com', 'root'}:
            part = 'center'
    else:
        part, role = _name_rule(pb.name)
    if part in {'arm', 'leg', 'finger'} and side == 'C':
        part, role = 'other', ''
    if part == 'finger':
        zone, group = 'Fingers ' + side, 'FINGER'
    elif part in {'arm', 'leg'}:
        zone, group = part.title() + ' ' + side, side
    elif part == 'spine':
        zone, group = 'Spine', 'C'
    elif part == 'center':
        zone = 'Head' if role in {'head', 'neck', 'neck_ik'} else 'COM'
        group = 'HEAD' if role == 'head' else 'COM' if role in {'com', 'root'} else 'C'
    else:
        zone, group = 'Other', side if side in {'L', 'R'} else 'C'
    return zone, group, part, role


def _name_rule(original):
    """Rig Studio가 아닌 리그. 이름 키워드로 템플릿 자리를 고른다."""
    name = original.lower()
    if _word(original, ('toes?',)):
        return 'leg', 'toe'
    if _finger(original):
        return 'finger', 'finger'
    ik = re.search(r'(^|[^a-z])ik([^a-z]|$)', name) is not None
    if 'pole' in name or 'elbow' in name or 'knee' in name:
        return ('arm' if re.search(r'arm|elbow|hand', name) else 'leg'), 'pole'
    if 'hand' in name or 'wrist' in name:
        return 'arm', ('ik' if ik else 'end_fk')
    if 'foot' in name or 'ankle' in name:
        return 'leg', ('ik' if ik else 'end_fk')
    if re.search(r'forearm|lower_?arm|lowerarm', name):
        return 'arm', 'lo_fk'
    if re.search(r'clavicle|shoulder', name):
        return 'arm', 'clavicle'
    if 'arm' in name:
        return 'arm', 'up_fk'
    if re.search(r'thigh|upper_?leg|upleg', name):
        return 'leg', 'up_fk'
    if re.search(r'shin|calf|lower_?leg|leg', name):
        return 'leg', 'lo_fk'
    if 'head' in name:
        return 'center', 'head'
    if 'neck' in name:
        return 'center', ('neck_ik' if ik else 'neck')
    if re.search(r'spine|chest|abdomen', name) or _word(original, ('torso',)):
        return 'spine', ('spine_ik' if ik else 'spine_fk')
    if re.search(r'pelvis|hips?([^a-z]|$)', name):
        return 'center', ('pelvis_ik' if ik else 'pelvis')
    if re.search(r'(^|[^a-z])(com|cog)([^a-z]|$)|center|centre|torso', name):
        return 'center', 'com'
    if re.search(r'master|traj', name):
        return 'center', 'root'
    if 'root' in name:
        # 흔한 이름 규칙에서 'root'만 있는 본은 골반 높이의 몸 중심 컨트롤이다.
        return 'center', 'com'
    return 'other', ''


def _slot_rect(zone, part, role, side):
    if part in {'arm', 'leg'}:
        slot = _LIMB[part].get(role)
        return (slot if side == 'L' else _mirror_x(slot)) if slot else None
    if part == 'center':
        return _CENTER.get(role)
    return None


def generate(arm):
    rig = is_rig_studio(arm)
    taken, spine, fingers, others = {}, defaultdict(list), defaultdict(lambda: defaultdict(list)), []
    output = []
    candidates = None if rig else _candidates(arm)
    for pb in arm.pose.bones:
        if rig and (bone_meta(pb) or {}).get('type') != 'CTRL':
            continue
        if not rig and pb.name not in candidates:
            continue
        zone, group, part, role = classify(pb, rig)
        side = zone[-1] if zone[-1] in 'LR' and zone != 'Other' else 'C'
        if part == 'spine':
            spine[role].append((pb.name, group))
            continue
        if part == 'finger':
            fingers[side][_finger(pb.name) or 'index'].append((pb.name, group))
            continue
        rect = _slot_rect(zone, part, role, side)
        key = (zone, role)
        if rect is None or key in taken:
            others.append((pb.name, 'Other', group))
            continue
        taken[key] = pb.name
        output.append(_button(pb.name, zone, group, rect, label=role.upper() if role in {'head', 'com'} else ''))
    output += _spine(spine, others)
    for side, chains in fingers.items():
        output += _hand(side, chains)
    output += _other(others)
    return output


def _candidates(arm):
    """일반 리그: 보이는 컬렉션의 본. 변형하지 않는 본(컨트롤)이 있으면 변형 본은 뺀다."""
    shown = [pb for pb in arm.pose.bones
             if not pb.bone.collections or any(c.is_visible_effectively for c in pb.bone.collections)]
    controls = [pb for pb in shown if not pb.bone.use_deform]
    return {pb.name for pb in (controls or shown)}


def _button(name, zone, group, rect, label=''):
    x, y, w, h, shape = rect
    tx, ty, tw, th = TEMPLATE
    return dict(bone=name, x=(x - tx) / tw, y=(y - ty) / th, w=w / tw, h=h / th,
                group=group, label=label, zone=zone, shape=shape)


MAX_SPINE = 8
MAX_JOINTS = 6


def _spine(spine, others):
    output = []
    x, y, w, h = _TORSO
    fk = sorted(spine.get('spine_fk', ()), key=lambda it: (_number(it[0]), it[0]))
    ik = sorted(spine.get('spine_ik', ()), key=lambda it: (_number(it[0]), it[0]))
    # 누를 수 있는 크기를 지키고 넘치는 본은 Other 줄로.
    others += [(n, 'Other', g) for n, g in fk[MAX_SPINE:] + ik[MAX_SPINE:]]
    fk, ik = fk[:MAX_SPINE], ik[:MAX_SPINE]
    rows = max(len(fk), len(ik), 1)
    step = h / rows
    for i, (name, group) in enumerate(fk):
        output.append(_button(name, 'Spine', group, (x, y + i * step + .5, w, step - 1.0, 'PLATE')))
    ix, size = _TORSO_IK
    size = min(size, step - 1.0)
    for i, (name, group) in enumerate(ik):
        output.append(_button(name, 'Spine', group,
                              (ix, y + i * step + (step - size) / 2, size, size, 'DIAMOND')))
    return output


def _hand(side, chains):
    """확대 손: 손끝이 아래로 향하고 엄지가 몸 쪽. 손가락 길이에 따라 부채꼴로 내려온다."""
    x, y, w, h = _HAND
    keys = [k for k in FINGERS if k in chains] + [k for k in chains if k not in FINGERS]
    columns = max(1, len(keys))
    col_w = w / columns
    reach = {'thumb': .58, 'index': .9, 'middle': 1.0, 'ring': .92, 'pinky': .76}
    drop = {'thumb': .30, 'index': .06, 'middle': 0.0, 'ring': .06, 'pinky': .16}
    output = []
    for col, key in enumerate(keys):
        joints = sorted(chains[key], key=lambda it: (_number(it[0]), it[0]))[:MAX_JOINTS]
        length = h * reach.get(key, .8)
        top = y + h - h * drop.get(key, .1)
        step = length / max(1, len(joints))
        for row, (name, group) in enumerate(joints):
            rect = (x + col * col_w + .5, top - (row + 1) * step + .4, col_w - 1.0, step - .8, 'BAR')
            output.append(_button(name, 'Fingers ' + side, group, rect if side == 'L' else _mirror_x(rect)))
    return output


MAX_OTHER_COLUMNS, MAX_OTHER_ROWS = 16, 3


def _other(items):
    """누를 수 있는 크기(가로 6·세로 2.1 단위 이상)까지만 싣는다. 넘치면 잘린다."""
    if not items:
        return []
    items = items[:MAX_OTHER_COLUMNS * MAX_OTHER_ROWS]
    x, y, w, h = _OTHER
    columns = min(MAX_OTHER_COLUMNS, len(items))
    rows = math.ceil(len(items) / columns)
    step_x, step_y = w / columns, h / rows
    return [_button(name, zone, group, (x + (i % columns) * step_x + .3, y + (rows - 1 - i // columns) * step_y + .3,
                                        step_x - .6, step_y - .6, 'BAR'))
            for i, (name, zone, group) in enumerate(items)]


def silhouette():
    """정규 좌표의 옅은 몸 실루엣 (x, y, w, h, radius 비율)."""
    tx, ty, tw, th = TEMPLATE
    shapes = []
    for x, y, w, h, r in SILHOUETTE:
        pieces = [(x, y, w, h)]
        if x + w / 2 > 50.5:
            pieces.append((100.0 - x - w, y, w, h))
        shapes += [((px - tx) / tw, (py - ty) / th, pw / tw, ph / th, r) for px, py, pw, ph in pieces]
    return shapes


def valid(buttons):
    try:
        return isinstance(buttons, list) and all(
            isinstance(b, dict) and isinstance(b.get('bone'), str) and isinstance(b.get('label'), str)
            and b.get('group') in {'L', 'R', 'C', 'COM', 'HEAD', 'FINGER'}
            and b.get('shape', 'BAR') in SHAPES
            and all(isinstance(b.get(k), (int, float)) and math.isfinite(b[k]) for k in ('x', 'y', 'w', 'h'))
            and 0 <= b['x'] < b['x'] + b['w'] <= 1 and 0 <= b['y'] < b['y'] + b['h'] <= 1
            for b in buttons)
    except (TypeError, KeyError):
        return False


def rebuild(arm):
    if not arm.data.is_editable:
        raise StudioError('편집할 수 있는 아마추어 데이터가 필요합니다.')
    buttons = generate(arm)
    if not valid(buttons):  # 저장 전에 그리기와 같은 기준으로 확인한다.
        raise StudioError('피커 배치를 만들지 못했습니다.')
    arm.data[PROPERTY] = json.dumps(buttons, ensure_ascii=False)
    return buttons


def layout(arm):
    raw = arm.data.get(PROPERTY)
    if raw is None:
        return generate(arm)  # 미저장 미리보기. 그리기 중 ID 쓰기 금지.
    try:
        buttons = json.loads(raw)
    except ValueError:
        buttons = None
    if not valid(buttons):
        raise StudioError('피커 배치가 손상되었습니다. Rebuild Layout을 실행하세요.')
    return buttons


def visible(pb):
    return (pb is not None and not pb.hide and not pb.bone.hide_select
            and (not pb.bone.collections or any(c.is_visible_effectively for c in pb.bone.collections)))


def enabled(arm):
    names = [pb.name for pb in arm.pose.bones if visible(pb)]
    return set(active_mode_controls(arm, names)[0])


def chip_names(arm, buttons, chip):
    if chip == 'Mirror':
        mapping = mirror.pairs(arm)
        return [mapping[pb.name] for pb in arm.pose.bones if pb.select]
    return [b['bone'] for b in buttons if chip == 'All' or
            b.get('zone', classify(arm.pose.bones[b['bone']], is_rig_studio(arm))[0]
                  if b['bone'] in arm.pose.bones else 'Other') == chip]


def select(arm, names, operation='SET'):
    allowed = enabled(arm)
    names = list(dict.fromkeys(n for n in names if n in allowed))
    before = {pb.name for pb in arm.pose.bones if pb.select}
    requested = set(names)
    after = {'SET': requested, 'ADD': before | requested, 'SUB': before - requested,
             'TOGGLE': before ^ requested}[operation]
    for pb in arm.pose.bones:
        pb.select = pb.name in after
    active = next((n for n in reversed(names) if n in after), None)
    if active is None and arm.data.bones.active and arm.data.bones.active.name in after:
        active = arm.data.bones.active.name
    if active is None:
        active = next((pb.name for pb in arm.pose.bones if pb.name in after), None)
    arm.data.bones.active = arm.data.bones.get(active) if active else None
    return after


def box_names(buttons, rectangle):
    x1, y1, x2, y2 = rectangle
    lo_x, hi_x, lo_y, hi_y = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)
    return [b['bone'] for b in buttons if b['x'] <= hi_x and b['x'] + b['w'] >= lo_x
            and b['y'] <= hi_y and b['y'] + b['h'] >= lo_y]
