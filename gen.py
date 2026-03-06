import bpy
import bmesh
import math
from mathutils import Vector, Matrix

def create_mi_plane(name="MI_Plane", size=1.0):
    """生成类似 Mine-imator 的直立平面，原点在底部中心"""
    mesh = bpy.data.meshes.new(name + "_Mesh")
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()
    # 生成基础平面 (size在create_grid中是半宽，所以 size/2)
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=size/2.0)

    # 1. 绕X轴旋转90度，使其直立面向Y轴
    rot_mat = Matrix.Rotation(math.radians(90.0), 4, 'X')
    bmesh.ops.transform(bm, matrix=rot_mat, verts=bm.verts)

    # 2. 向上平移一半的高度，让底部边缘对齐到原点 (Z=0)
    trans_mat = Matrix.Translation((0.0, 0.0, size/2.0))
    bmesh.ops.transform(bm, matrix=trans_mat, verts=bm.verts)

    bm.to_mesh(mesh)
    bm.free()
    
    # 选中新生成的物体
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj

def create_mi_cube(name="MI_Cube", size=1.0):
    """生成类似 Mine-imator 的立方体，原点在底面中心"""
    mesh = bpy.data.meshes.new(name + "_Mesh")
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()
    # 生成基础立方体
    bmesh.ops.create_cube(bm, size=size)

    # 向上平移一半的高度，让底面落在原点上 (Z=0)
    trans_mat = Matrix.Translation((0.0, 0.0, size/2.0))
    bmesh.ops.transform(bm, matrix=trans_mat, verts=bm.verts)

    bm.to_mesh(mesh)
    bm.free()
    
    # 选中新生成的物体
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj

# 执行函数测试生成
# 在调用前先清除选中项，方便观察
bpy.ops.object.select_all(action='DESELECT')

# 生成一个平面和一个立方体，并将立方体稍微向旁边移动一点以免重叠
mi_plane = create_mi_plane(name="Surface")
mi_cube = create_mi_cube(name="Cube")
mi_cube.location.x = 1.5