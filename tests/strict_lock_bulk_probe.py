"""Disposable foreach_get experiment. Does not change or register the real addon.

-- capabilities | regression | benchmark optimized | benchmark bulk
Use the same input blend for benchmark variants in separate factory processes.
"""
import ast
from array import array
import bpy
import json
from pathlib import Path
import sys

assert bpy.app.background, 'Disposable background process required'
ROOT = Path(__file__).resolve().parents[1]
ARGS = sys.argv[sys.argv.index('--')+1:]


def harness():
    path = ROOT/'tests/strict_lock_regression.py'
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    definitions = []
    for node in tree.body:
        if isinstance(node, ast.If):
            break  # Exclude the command runner while reusing the same regression cases.
        definitions.append(node)
    tree.body = definitions
    scope = {'__file__':str(path), '__name__':'strict_lock_probe_harness'}
    exec(compile(tree, str(path), 'exec'), scope)
    return scope


# This function is injected into a newly imported scratch module's globals only.
# No function, handler, snapshot or source in the real addon is modified.
BULK_OWNERS_SOURCE = '''
from array import array

def _owners(only_ids=None):
    scene = bpy.context.scene
    if scene is None:
        return
    tracked = {}
    for key in {sk[0] for sk in _snap}:
        if key[0] == "PB":
            tracked.setdefault(key[1], set()).add(key[2])
    for obj in scene.objects:
        if only_ids is not None and obj.name not in only_ids:
            continue
        yield ("OB", obj.name), obj, obj, ""
        if obj.type != 'ARMATURE' or obj.pose is None:
            continue
        bones = obj.pose.bones
        n = len(bones)
        if not n:
            continue
        location = array('b', [0]) * (n * 3)
        scale = array('b', [0]) * (n * 3)
        rotation = array('b', [0]) * (n * 3)
        four_d = array('b', [0]) * n
        rotation_w = array('b', [0]) * n
        try:
            bones.foreach_get('lock_location', location)
            bones.foreach_get('lock_scale', scale)
            bones.foreach_get('lock_rotation', rotation)
            bones.foreach_get('lock_rotations_4d', four_d)
            bones.foreach_get('lock_rotation_w', rotation_w)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            # Compatibility fallback preserves every owner if bulk RNA is unsupported.
            indices = range(n)
        else:
            selected = set()
            for i in range(n):
                j = i * 3
                if (location[j] or location[j+1] or location[j+2]
                    or scale[j] or scale[j+1] or scale[j+2]
                    or rotation[j] or rotation[j+1] or rotation[j+2]
                    or (four_d[i] and rotation_w[i])):
                    selected.add(i)
            # A now-unlocked owner must still reach _enforce to free old F-Curves.
            for name in tracked.get(obj.name, ()):
                i = bones.find(name)
                if i != -1:
                    selected.add(i)
            indices = sorted(selected)
        for i in indices:
            pb = bones[i]
            yield ("PB", obj.name, pb.name), pb, obj, 'pose.bones["{:s}"].'.format(pb.name)
'''


def capabilities():
    rig = bpy.data.objects.get('RigStudio')
    if rig is None:
        _, _, rig = harness()['fixture']()
        pb = rig.pose.bones['Control']
        pb.lock_location = (True, False, True)
        pb.lock_scale = (False, True, False)
        pb.lock_rotation = (True, False, False)
        pb.lock_rotations_4d = True
        pb.lock_rotation_w = True
    bones = rig.pose.bones
    report = {'version':bpy.app.version_string,'bone_count':len(bones),'buffers':{}}
    for kind in ('bytearray', 'b', 'i'):
        entries = {}
        for prop, width in (('lock_location',3),('lock_scale',3),('lock_rotation',3),
                            ('lock_rotations_4d',1),('lock_rotation_w',1)):
            buffer = bytearray(len(bones)*width) if kind=='bytearray' else array(kind,[0])*(len(bones)*width)
            expected = [bool(x) for pb in bones for x in (getattr(pb,prop)[:] if width==3 else [getattr(pb,prop)])]
            try:
                bones.foreach_get(prop, buffer)
                entries[prop] = {'supported':True,'equal':list(map(bool,buffer))==expected,
                                 'locked_count':sum(map(bool,buffer))}
            except Exception as exc:
                entries[prop] = {'supported':False,'error':repr(exc)}
        report['buffers'][kind] = entries
    return report


h = harness()
if ARGS[0] == 'capabilities':
    report = capabilities()
    suffix = 'capabilities'
elif ARGS[0] == 'regression':
    report = {}
    for variant in ('optimized','bulk'):
        mod,prefs = h['module']('optimized')
        if variant == 'bulk':
            exec(BULK_OWNERS_SOURCE, mod.__dict__)
        report[variant] = h['regression'](mod,prefs)
    assert report['optimized'] == report['bulk']
    suffix = 'regression'
else:
    assert ARGS[0] == 'benchmark' and ARGS[1] in ('optimized','bulk')
    variant = ARGS[1]
    mod,prefs = h['module']('optimized')
    if variant == 'bulk':
        exec(BULK_OWNERS_SOURCE, mod.__dict__)
    report = {'variant':variant,'benchmark':h['benchmark'](mod)}
    suffix = 'benchmark_'+variant
outfile = ROOT/('tests/strict_lock_bulk_'+suffix+'.json')
outfile.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('STRICT_LOCK_BULK_PROBE',outfile,flush=True)
