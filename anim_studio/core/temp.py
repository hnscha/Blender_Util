# SPDX-License-Identifier: GPL-3.0-or-later
from .errors import StudioError
from contextlib import contextmanager

import bpy
from bpy.app.handlers import persistent

COLLECTION = "AnimStudio_Temp"
PREFIX = "AS_TEMP_"


def _remove_empty_collection():
    collection = bpy.data.collections.get(COLLECTION)
    if collection is not None and not collection.objects and not collection.children:
        bpy.data.collections.remove(collection)


def remove_constraints(arm, bones):
    """베이크가 월드 목표를 측정한 뒤 임시 제약만 해제한다."""
    count = 0
    for name in bones:
        pb = arm.pose.bones[name]
        for constraint in list(pb.constraints):
            if constraint.name.startswith(PREFIX):
                pb.constraints.remove(constraint)
                count += 1
    return count


class TempSession:
    def __init__(self):
        self.objects = []
        self.constraints = []
        self.kept = False

    def empty(self, name, matrix, display='PLAIN_AXES', size=1.0):
        collection = bpy.data.collections.get(COLLECTION)
        if collection is None:
            collection = bpy.data.collections.new(COLLECTION)
        scene = bpy.context.scene
        if collection.name not in scene.collection.children:
            scene.collection.children.link(collection)
        collection.hide_viewport = False
        collection.hide_render = True
        obj = bpy.data.objects.new(name, None)
        self.objects.append(obj.as_pointer())
        obj["anim_studio_temp"] = True
        collection.objects.link(obj)
        obj.empty_display_type = display
        obj.empty_display_size = size
        obj.matrix_world = matrix
        return obj

    def constraint(self, pbone, type, **kw):
        constraint = pbone.constraints.new(type)
        constraint.name = PREFIX + type
        self.constraints.append((pbone.id_data.as_pointer(), pbone.as_pointer(), constraint.as_pointer()))
        for name, value in kw.items():
            if name == 'name':
                raise StudioError("임시 컨스트레인트 이름은 엔진이 지정합니다.")
            setattr(constraint, name, value)
        return constraint

    def keep(self):
        self.kept = True

    def cleanup(self):
        owners = {(obj.as_pointer(), pb.as_pointer()): pb
                  for obj in bpy.data.objects if obj.type == 'ARMATURE' for pb in obj.pose.bones}
        for arm_pointer, bone_pointer, pointer in reversed(self.constraints):
            pb = owners.get((arm_pointer, bone_pointer))
            if pb is not None:
                for constraint in list(pb.constraints):
                    if constraint.as_pointer() == pointer:
                        pb.constraints.remove(constraint)
        for obj in list(bpy.data.objects):
            if obj.as_pointer() in self.objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        self.constraints.clear()
        self.objects.clear()
        _remove_empty_collection()


@contextmanager
def session():
    temp = TempSession()
    try:
        yield temp
    except BaseException:
        temp.cleanup()
        raise
    else:
        if not temp.kept:
            temp.cleanup()


def cleanup_all():
    count = 0
    for obj in bpy.data.objects:
        owners = [obj]
        if obj.type == 'ARMATURE':
            owners.extend(obj.pose.bones)
        for owner in owners:
            for constraint in list(owner.constraints):
                if constraint.name.startswith(PREFIX):
                    owner.constraints.remove(constraint)
                    count += 1
    for obj in list(bpy.data.objects):
        if obj.get("anim_studio_temp", False):
            bpy.data.objects.remove(obj, do_unlink=True)
            count += 1
    _remove_empty_collection()
    return count


@persistent
def _load_post(_unused):
    count = cleanup_all()
    if count:
        print(f"Anim Studio: 임시 항목 {count}개 정리")


def register():
    bpy.app.handlers.load_post.append(_load_post)


def unregister():
    bpy.app.handlers.load_post.remove(_load_post)
    cleanup_all()
