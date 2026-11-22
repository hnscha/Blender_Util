"""Background-only behavior regression and callback benchmark; never saves a blend.

-- regression | benchmark baseline | benchmark optimized
Baseline defaults to the pre-change Git blob; --baseline PATH overrides it.
"""
import bpy
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
ARGS = sys.argv[sys.argv.index('--')+1:]
BASELINE = Path(ARGS[ARGS.index('--baseline')+1]) if '--baseline' in ARGS else None
BASELINE_BLOB = '597b37a01f15893ce2e247b498ea3f0f9a1343f8'
assert bpy.app.background, 'Disposable background process required'


def module(variant):
    name = 'strict_lock_test_' + variant
    if variant == 'baseline' and BASELINE is None:
        source = subprocess.check_output(
            ['git', '-c', 'safe.directory='+str(ROOT), 'show', BASELINE_BLOB], cwd=ROOT)
        mod = types.ModuleType(name)
        mod.__file__ = 'git-blob:' + BASELINE_BLOB
        mod.__package__ = ''
        sys.modules[name] = mod
        exec(compile(source, mod.__file__, 'exec'), mod.__dict__)
    else:
        path = BASELINE if variant == 'baseline' else ROOT/'strict_lock/__init__.py'
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    prefs = types.SimpleNamespace(enabled=True, lock_fcurves=True, remove_new_keys=True)
    mod._prefs = lambda: prefs
    mod._report = lambda text: mod._recent.append(text)
    return mod, prefs


def fixture():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = bpy.data.objects.new('LockTest', None)
    other = bpy.data.objects.new('Other', None)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.scene.collection.objects.link(other)
    arm = bpy.data.armatures.new('ArmData')
    rig = bpy.data.objects.new('Arm', arm)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bone = arm.edit_bones.new('Control'); bone.tail = (0, 1, 0)
    bone = arm.edit_bones.new('Free'); bone.tail = (1, 1, 0)
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj, other, rig


def regression(mod, prefs):
    checks = []
    def check(label, condition):
        assert condition, label
        checks.append(label)
    obj, other, rig = fixture()
    # Compare every transform kind on objects and bones, including 4D W flags.
    for owner in (obj, rig.pose.bones['Control']):
        for mode in ('XYZ', 'QUATERNION', 'AXIS_ANGLE'):
            owner.rotation_mode = mode
            owner.lock_location = (True, False, True)
            owner.lock_scale = (True, True, False)
            owner.lock_rotation = (True, False, True)
            owner.lock_rotations_4d = True
            owner.lock_rotation_w = True
            mod.recapture_all()
            watched = [(path, index, getattr(owner, path)[index])
                       for path, index, locked in mod._channels(owner) if locked]
            for path, index, value in watched:
                getattr(owner, path)[index] = value + .7
            mod._enforce()
            check(owner.name+':'+mode, all(abs(getattr(owner, path)[index]-value)<1e-6
                                          for path, index, value in watched))
            owner.lock_location = owner.lock_scale = owner.lock_rotation = (False, False, False)
            owner.lock_rotations_4d = False
            mod._enforce()
    check('all-unlocked snapshots removed', len(mod._snap) == 0)
    # Selective owner filtering must neither enforce nor forget another object.
    obj.location.x = 2; other.location.x = 3
    obj.lock_location[0] = other.lock_location[0] = True
    mod.recapture_all()
    obj.location.x = 8; other.location.x = 9
    mod._enforce({obj.name})
    check('only_ids applies only requested object', obj.location.x == 2 and other.location.x == 9)
    mod._enforce()
    check('other snapshot preserved', other.location.x == 3)
    # Unlock frees its F-Curve; relock captures the value present at that time.
    obj.location.x = 2
    obj.keyframe_insert('location', index=0, frame=1)
    obj.location.x = 4
    obj.keyframe_insert('location', index=0, frame=10)
    fc = mod._fcurve(obj, 'location', 0)
    for key in fc.keyframe_points:
        key.interpolation = 'LINEAR'; key.handle_left_type = key.handle_right_type = 'FREE'
    mod.recapture_all()
    saved = mod._keys_of(fc)
    check('existing curve locked', fc.lock)
    fc.keyframe_points[0].co.y = 99
    fc.keyframe_points[0].handle_left.y += 10
    mod._enforce()
    check('curve value and handle restored', mod._keys_of(fc) == saved)
    mod._channelbag(obj).fcurves.remove(fc)
    mod._enforce()
    fc = mod._fcurve(obj, 'location', 0)
    check('deleted curve restored', mod._keys_of(fc) == saved and fc.lock)
    bpy.context.scene.frame_set(5)
    obj.location.x = 99
    mod._enforce()
    check('typed value follows existing curve', abs(obj.location.x-fc.evaluate(5))<1e-5)
    fc.mute = True; obj.location.x = 99; mod._enforce()
    check('muted curve does not force evaluated value', obj.location.x == 99)
    fc.mute = False
    track = obj.animation_data.nla_tracks.new()
    obj.location.x = 88; mod._enforce()
    check('NLA skips final value comparison', obj.location.x == 88)
    obj.animation_data.nla_tracks.remove(track)
    obj.lock_location[0] = False; mod._enforce()
    check('unlock frees F-Curve', not fc.lock and (('OB',obj.name),'location',0) not in mod._snap)
    mod._channelbag(obj).fcurves.remove(fc)
    obj.location.x = 7; obj.lock_location[0] = True; mod._enforce()
    obj.location.x = 11; mod._enforce()
    check('relock captures new value', obj.location.x == 7)
    obj.location.x = 12; obj.keyframe_insert('location',index=0,frame=5); mod._enforce()
    check('new locked curve removed', mod._fcurve(obj,'location',0) is None and obj.location.x == 7)
    prefs.remove_new_keys = False
    obj.location.x = 13; obj.keyframe_insert('location',index=0,frame=5); mod._enforce()
    check('new keys adopted when allowed', mod._fcurve(obj,'location',0) is not None)
    prefs.remove_new_keys = True
    # Drivers may be added/deleted between callbacks: no stale skip decisions.
    obj, other, rig = fixture(); mod._snap.clear()
    obj.location.x = 2; obj.lock_location[0] = True; mod.recapture_all()
    driver = obj.driver_add('location',0); driver.driver.expression = '4.0'
    obj.location.x = 99; mod._enforce()
    check('new driver excluded immediately', obj.location.x == 99)
    obj.driver_remove('location',0); mod._enforce()
    check('removed driver protected immediately', obj.location.x == 2)
    # Bone unlock path is retained even though fully unlocked new bones are skipped.
    bone = rig.pose.bones['Control']; bone.location.x = 3; bone.lock_location[0] = True
    bone.keyframe_insert('location',index=0,frame=1); mod.recapture_all()
    fc = mod._fcurve(rig,'pose.bones["Control"].location',0)
    bone.lock_location[0] = False; mod._enforce({rig.name})
    check('all-unlocked tracked bone frees curve', not fc.lock)
    bone.lock_rotation[0] = True; bone.rotation_mode = 'XYZ'; mod.recapture_all()
    bone.rotation_mode = 'QUATERNION'; mod._enforce()
    check('rotation mode changes rebuilt each pass',
          (('PB',rig.name,bone.name),'rotation_quaternion',1) in mod._snap and
          (('PB',rig.name,bone.name),'rotation_euler',0) not in mod._snap)
    # Deletion/load-like replacement has no cached owner pointers or cached lock flags.
    other.lock_scale[0] = True; mod.recapture_all()
    bpy.data.objects.remove(other, do_unlink=True); mod._enforce()
    check('deleted owner removed', not any(sk[0][1]=='Other' for sk in mod._snap))
    obj, other, rig = fixture(); mod.recapture_all()
    obj.lock_location[2] = True; obj.location.z = 6; mod._enforce()
    obj.location.z = 9; mod._enforce()
    check('fresh load owner recognized', obj.location.z == 6)
    # Undo rebuilds RNA; the callback resolves owners again from the current scene.
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message='lock before')
    obj.lock_scale[1] = True; obj.scale.y = 2
    bpy.ops.ed.undo_push(message='lock after')
    bpy.ops.ed.undo(); mod._enforce()
    obj = bpy.data.objects['LockTest']; obj.location.z = 12; mod._enforce()
    check('Undo rebuilt owners still enforced', obj.location.z == 6)
    return checks


def stats(values):
    return {'n':len(values),'median_ms':statistics.median(values),
            'p95_ms':sorted(values)[int(.95*(len(values)-1))]}


def benchmark(mod):
    assert not bpy.app.handlers.depsgraph_update_post
    mod.recapture_all()
    calls = []
    def callback(*args):
        start = time.perf_counter()
        try: mod._on_depsgraph(*args)
        finally: calls.append((time.perf_counter()-start)*1000)
    bpy.app.handlers.depsgraph_update_post.append(callback)
    rig = bpy.data.objects['RigStudio']
    result = {}
    for name in ('Ctrl_C_HeadSquash_02','Ctrl_L_WingHand'):
        bone = rig.pose.bones[name]; original = bone.location.copy(); values = []
        for i in range(58):
            if i == 8: calls.clear()
            bone.location.z = original.z + (.0001 if i%2 else -.0001)
            start = time.perf_counter(); bpy.context.view_layer.update()
            if i >= 8: values.append((time.perf_counter()-start)*1000)
        result[name] = {'update':stats(values),'callback':stats(calls)}
        bone.location = original; bpy.context.view_layer.update()
    bpy.app.handlers.depsgraph_update_post.remove(callback)
    return result


if ARGS[0] == 'regression':
    report = {}
    for variant in ('baseline','optimized'):
        mod,prefs = module(variant)
        report[variant] = regression(mod,prefs)
    assert report['baseline'] == report['optimized']
    outfile = ROOT/'tests/strict_lock_regression_result.json'
else:
    assert ARGS[0] == 'benchmark'
    variant = ARGS[1]
    mod,prefs = module(variant)
    report = {'variant':variant,'benchmark':benchmark(mod)}
    outfile = ROOT/('tests/strict_lock_benchmark_'+variant+'.json')
outfile.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('STRICT_LOCK_TEST',outfile,flush=True)
