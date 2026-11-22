# Anim Studio 전용 아이콘 생성 (직접 그림, 외부 아이콘 미사용). 128px 로 그려 32px 로 축소.
import math, os, sys
from PIL import Image, ImageDraw, ImageFont

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
S = 128
W = 9            # 기본 선 두께(128 기준)
FG = (226, 226, 226, 255)
C = {
    "key": (240, 190, 80, 255), "del": (232, 104, 88, 255), "xf": (110, 180, 238, 255),
    "sel": (140, 208, 120, 255), "vis": (184, 156, 240, 255), "view": (238, 160, 90, 255),
    "dim": (150, 150, 150, 255),
}

def canvas():
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)

def save(im, name):
    im.resize((32, 32), Image.LANCZOS).save(os.path.join(OUT, name + ".png"))
    im.resize((64, 64), Image.LANCZOS).save(os.path.join(OUT, name + "@2x.png"))

def diamond(d, cx, cy, r, fill=None, outline=None, w=W):
    pts = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    d.polygon(pts, fill=fill)
    if outline:
        d.line(pts + [pts[0]], fill=outline, width=w, joint="curve")

def arrow(d, x0, y0, x1, y1, col, w=W, head=18):
    d.line([(x0, y0), (x1, y1)], fill=col, width=w)
    a = math.atan2(y1 - y0, x1 - x0)
    for s in (-1, 1):
        d.line([(x1, y1), (x1 - head * math.cos(a + s * 0.6), y1 - head * math.sin(a + s * 0.6))], fill=col, width=w)

def xmark(d, cx, cy, r, col, w=W):
    d.line([(cx - r, cy - r), (cx + r, cy + r)], fill=col, width=w)
    d.line([(cx - r, cy + r), (cx + r, cy - r)], fill=col, width=w)

def bone(d, x0, y0, x1, y1, col, w=W):
    # 바이패드식 본: 두 끝 원 + 가운데 마름모 몸통
    d.ellipse([x0 - 9, y0 - 9, x0 + 9, y0 + 9], outline=col, width=w - 3)
    d.ellipse([x1 - 6, y1 - 6, x1 + 6, y1 + 6], outline=col, width=w - 3)
    a = math.atan2(y1 - y0, x1 - x0); L = math.hypot(x1 - x0, y1 - y0)
    px, py = -math.sin(a), math.cos(a)
    mx, my = x0 + math.cos(a) * L * 0.28, y0 + math.sin(a) * L * 0.28
    pts = [(x0 + math.cos(a) * 9, y0 + math.sin(a) * 9), (mx + px * 14, my + py * 14),
           (x1 - math.cos(a) * 6, y1 - math.sin(a) * 6), (mx - px * 14, my - py * 14)]
    d.line(pts + [pts[0]], fill=col, width=w - 3, joint="curve")

def font(sz):
    for f in ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, sz)
    return ImageFont.load_default()

def text(d, x, y, s, col, sz=40):
    d.text((x, y), s, fill=col, font=font(sz), anchor="mm")

icons = {}
def icon(name):
    def deco(fn):
        icons[name] = fn
        return fn
    return deco

# ---------------- 키
@icon("key_all")
def _(d):
    for cx, cy in ((40, 44), (88, 44), (40, 92), (88, 92)):
        diamond(d, cx, cy, 20, fill=C["key"])

@icon("key_selected")
def _(d):
    diamond(d, 56, 60, 40, fill=C["key"])
    d.polygon([(84, 72), (84, 120), (96, 106), (112, 122), (120, 114), (104, 98), (120, 92)], fill=FG)

@icon("key_track")
def _(d):
    diamond(d, 46, 50, 36, fill=C["key"])
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        arrow(d, 96, 96, 96 + dx * 26, 96 + dy * 26, FG, w=7, head=11)

@icon("del_track")
def _(d):
    diamond(d, 50, 64, 38, outline=C["key"])
    xmark(d, 100, 100, 16, C["del"], w=11)

@icon("del_selected")
def _(d):
    diamond(d, 64, 64, 48, outline=C["key"])
    xmark(d, 64, 64, 18, C["del"], w=11)

@icon("del_outside")
def _(d):
    d.line([(44, 26), (44, 102)], fill=FG, width=W); d.line([(84, 26), (84, 102)], fill=FG, width=W)
    diamond(d, 64, 64, 14, fill=C["key"])
    xmark(d, 18, 64, 11, C["del"], w=9); xmark(d, 110, 64, 11, C["del"], w=9)

@icon("del_all")
def _(d):
    d.line([(10, 64), (118, 64)], fill=C["dim"], width=6)
    for x in (26, 64, 102):
        diamond(d, x, 64, 14, fill=C["key"])
    d.line([(14, 108), (114, 20)], fill=C["del"], width=12)

# ---------------- 변환
def globe(d, cx, cy, r, col):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=W - 2)
    d.ellipse([cx - r * 0.42, cy - r, cx + r * 0.42, cy + r], outline=col, width=W - 3)
    d.line([(cx - r, cy), (cx + r, cy)], fill=col, width=W - 3)

def localaxes(d, ox, oy, col):
    d.line([(ox, oy), (ox + 34, oy - 18)], fill=(230, 90, 90, 255), width=W - 1)
    d.line([(ox, oy), (ox + 6, oy - 40)], fill=(120, 210, 110, 255), width=W - 1)
    d.ellipse([ox - 6, oy - 6, ox + 6, oy + 6], fill=col)

@icon("toggle_world_local")
def _(d):
    globe(d, 40, 40, 30, C["xf"]); localaxes(d, 84, 108, FG)
    d.arc([30, 30, 98, 98], start=300, end=60, fill=FG, width=6)

@icon("move_world_local")
def _(d):
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        arrow(d, 64, 64, 64 + dx * 50, 64 + dy * 50, C["xf"])
    d.ellipse([54, 54, 74, 74], fill=FG)

@icon("rotate_world_local")
def _(d):
    d.arc([16, 16, 112, 112], start=200, end=500, fill=C["xf"], width=W + 1)
    a = math.radians(500 - 360)
    x, y = 64 + 48 * math.cos(a), 64 + 48 * math.sin(a)
    d.polygon([(x - 4, y - 22), (x + 20, y + 4), (x - 18, y + 14)], fill=C["xf"])
    d.ellipse([54, 54, 74, 74], fill=FG)

@icon("scale_world_local")
def _(d):
    d.rectangle([22, 54, 74, 106], outline=FG, width=W - 2)
    d.rectangle([22, 22, 106, 106], outline=C["xf"], width=W - 2)
    arrow(d, 54, 74, 96, 32, C["xf"])

@icon("free_rotation")
def _(d):
    d.ellipse([20, 20, 108, 108], outline=FG, width=W - 2)
    d.arc([20, 46, 108, 82], start=0, end=360, fill=C["xf"], width=W - 2)
    d.arc([46, 20, 82, 108], start=0, end=360, fill=C["xf"], width=W - 2)

# ---------------- 선택
@icon("select_opposite")
def _(d):
    for y in range(10, 120, 18):
        d.line([(64, y), (64, y + 9)], fill=C["dim"], width=5)
    bone(d, 18, 100, 50, 28, C["sel"], w=W + 1); bone(d, 110, 100, 78, 28, FG, w=W + 1)

@icon("select_com")
def _(d):
    d.ellipse([22, 22, 106, 106], outline=C["sel"], width=W)
    d.line([(64, 6), (64, 40)], fill=C["sel"], width=W); d.line([(64, 88), (64, 122)], fill=C["sel"], width=W)
    d.line([(6, 64), (40, 64)], fill=C["sel"], width=W); d.line([(88, 64), (122, 64)], fill=C["sel"], width=W)
    d.ellipse([52, 52, 76, 76], fill=FG)

@icon("next_armature")
def _(d):
    bone(d, 18, 96, 70, 28, C["sel"])
    arrow(d, 78, 82, 118, 82, FG); arrow(d, 78, 82, 118, 82, FG)

@icon("choose_armature")
def _(d):
    for i, y in enumerate((26, 64, 102)):
        d.ellipse([12, y - 9, 30, y + 9], fill=C["sel"] if i == 1 else C["dim"])
        d.line([(42, y), (116, y)], fill=FG if i == 1 else C["dim"], width=W)

@icon("easy_parent")
def _(d):
    d.rounded_rectangle([10, 44, 70, 84], radius=20, outline=C["sel"], width=W)
    d.rounded_rectangle([58, 44, 118, 84], radius=20, outline=FG, width=W)

# ---------------- 표시
def cube(d, col, dashed=False):
    f = [(30, 48), (78, 48), (78, 100), (30, 100)]
    b = [(p[0] + 22, p[1] - 22) for p in f]
    segs = [(f[i], f[(i + 1) % 4]) for i in range(4)] + [(b[i], b[(i + 1) % 4]) for i in (3, 0)] + [(f[i], b[i]) for i in (0, 1, 2)]
    segs += [(b[1], b[2])]
    for a, z in segs:
        if dashed:
            n = 5
            for k in range(n):
                if k % 2 == 0:
                    t0, t1 = k / n, (k + 1) / n
                    d.line([(a[0] + (z[0] - a[0]) * t0, a[1] + (z[1] - a[1]) * t0), (a[0] + (z[0] - a[0]) * t1, a[1] + (z[1] - a[1]) * t1)], fill=col, width=W - 1)
        else:
            d.line([a, z], fill=col, width=W - 1)

@icon("vis_geometry")
def _(d): cube(d, C["vis"])

@icon("vis_non_geometry")
def _(d): cube(d, C["vis"], dashed=True)

@icon("vis_bones")
def _(d): bone(d, 26, 104, 102, 24, C["vis"], w=W + 2)

@icon("vis_helpers")
def _(d):
    for a in (0, 90, 45, 135):
        r = math.radians(a); L = 52 if a % 90 == 0 else 30
        d.line([(64 - L * math.cos(r), 64 - L * math.sin(r)), (64 + L * math.cos(r), 64 + L * math.sin(r))], fill=C["vis"], width=W)

@icon("vis_shapes")
def _(d):
    pts = [(14 + t * 100, 64 - 40 * math.sin(t * math.pi * 2)) for t in [i / 40 for i in range(41)]]
    d.line(pts, fill=C["vis"], width=W, joint="curve")
    for p in (pts[0], pts[-1]):
        d.ellipse([p[0] - 8, p[1] - 8, p[0] + 8, p[1] + 8], fill=FG)

@icon("vis_cameras")
def _(d):
    d.rounded_rectangle([12, 40, 84, 96], radius=10, outline=C["vis"], width=W)
    d.polygon([(84, 60), (116, 42), (116, 94), (84, 76)], outline=C["vis"], fill=None)
    d.line([(84, 60), (116, 42), (116, 94), (84, 76)], fill=C["vis"], width=W, joint="curve")

# ---------------- 보기
@icon("view_left_right")
def _(d):
    arrow(d, 64, 64, 12, 64, C["view"]); arrow(d, 64, 64, 116, 64, C["view"])
    text(d, 64, 32, "L|R", FG, 34)

@icon("view_top_bottom")
def _(d):
    arrow(d, 64, 64, 64, 10, C["view"]); arrow(d, 64, 64, 64, 118, C["view"])
    text(d, 30, 64, "T", FG, 38); text(d, 98, 64, "B", FG, 38)

@icon("view_front_back")
def _(d):
    arrow(d, 64, 64, 22, 106, C["view"]); arrow(d, 64, 64, 106, 22, C["view"])
    text(d, 30, 34, "F", FG, 38); text(d, 98, 96, "B", FG, 38)

@icon("safe_frames")
def _(d):
    d.rectangle([8, 24, 120, 104], outline=FG, width=W - 2)
    d.rectangle([26, 38, 102, 90], outline=C["view"], width=W - 2)

@icon("thirds_guide")
def _(d):
    d.rectangle([8, 24, 120, 104], outline=FG, width=W - 2)
    for x in (45, 83):
        d.line([(x, 24), (x, 104)], fill=C["view"], width=W - 3)
    for y in (51, 77):
        d.line([(8, y), (120, y)], fill=C["view"], width=W - 3)

@icon("easy_resolution")
def _(d):
    d.rectangle([8, 28, 120, 100], outline=C["view"], width=W - 1)
    text(d, 64, 64, "16:9", FG, 34)

@icon("pose_library")
def _(d):
    d.rounded_rectangle([14, 16, 114, 112], radius=10, outline=C["view"], width=W - 2)
    d.ellipse([54, 30, 74, 50], fill=FG)
    d.line([(64, 50), (64, 80)], fill=FG, width=W); d.line([(40, 58), (88, 50)], fill=FG, width=W)
    d.line([(64, 80), (46, 102)], fill=FG, width=W); d.line([(64, 80), (84, 100)], fill=FG, width=W)

@icon("focus_panel")
def _(d):
    d.rectangle([8, 18, 120, 110], outline=FG, width=W - 2)
    d.rectangle([80, 18, 120, 110], fill=C["view"])
    for y in (40, 62, 84):
        d.line([(88, y), (112, y)], fill=(30, 30, 30, 255), width=6)

@icon("keymap")
def _(d):
    d.rounded_rectangle([6, 34, 122, 98], radius=10, outline=FG, width=W - 2)
    for row, y in enumerate((52, 76)):
        for x in range(22, 112, 22):
            d.rectangle([x - 7, y - 7, x + 7, y + 7], fill=C["view"] if (row, x) == (0, 66) else C["dim"])

@icon("anim_studio")
def _(d):
    d.rounded_rectangle([6, 6, 122, 122], radius=26, fill=(47, 106, 153, 255))
    diamond(d, 64, 64, 34, fill=C["key"])
    d.arc([22, 22, 106, 106], start=220, end=320, fill=FG, width=8)

for name, fn in icons.items():
    im, d = canvas(); fn(d); save(im, name)

# 미리보기 시트 (어두운 배경, 실제 크기 32px + 확대 64px)
names = list(icons)
cols = 6; cw, ch = 210, 110
sheet = Image.new("RGB", (cols * cw, math.ceil(len(names) / cols) * ch + 10), (43, 43, 43))
sd = ImageDraw.Draw(sheet); f = font(15)
for i, n in enumerate(names):
    x, y = (i % cols) * cw + 10, (i // cols) * ch + 10
    sheet.paste(Image.open(os.path.join(OUT, n + "@2x.png")), (x, y), Image.open(os.path.join(OUT, n + "@2x.png")))
    sm = Image.open(os.path.join(OUT, n + ".png")); sheet.paste(sm, (x + 80, y + 16), sm)
    sm16 = sm.resize((16, 16), Image.LANCZOS); sheet.paste(sm16, (x + 124, y + 24), sm16)
    sd.text((x, y + 72), n, fill=(210, 210, 210), font=f)
sheet.save(os.path.join(os.path.dirname(OUT.rstrip("/\\")), "icon_sheet.png"))
print("ICONS", len(names))
