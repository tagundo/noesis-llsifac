#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_scene — engine-neutral scene data shared by the parser and the FBX writer
==============================================================================

The parser (:mod:`sifac_bmarc`) decodes a ``.bmarc`` into these plain
dataclasses; the FBX builder (:mod:`sifac_fbx`) turns them into an FBX
document.  Nothing here knows about Noesis or FBX — it is just geometry,
skinning, animation and material data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from sifac_mathutil import Mat4, Quat, Vec3


@dataclass
class Bone:
    index: int
    name: str
    parent_name: Optional[str]
    local_matrix: Mat4                 # parent-relative (Noesis NODE transform)
    parent_index: int = -1
    # Filled in by the parser once the whole skeleton is known:
    world_matrix: Optional[Mat4] = None


@dataclass
class Material:
    name: str
    diffuse_tex: str = ""
    normal_tex: str = ""
    specular_tex: str = ""
    light_tex: str = ""
    env_tex: str = ""
    ambient: Tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)

    def texture_names(self) -> List[str]:
        return [t for t in (self.diffuse_tex, self.normal_tex,
                            self.specular_tex, self.light_tex, self.env_tex) if t]


@dataclass
class MorphTarget:
    name: str
    # Per-vertex deltas relative to the base mesh (same vertex order/count).
    position_deltas: List[Vec3] = field(default_factory=list)
    normal_deltas: List[Vec3] = field(default_factory=list)


@dataclass
class SubMesh:
    """One DRAW call: a chunk of geometry with a single material.

    Positions/normals are the **raw rest-space** vertices from the buffer (the
    mesh's own local space).  ``bind_transform`` is the mesh's global bind
    matrix that maps that local space into world (the game's per-mesh
    pre-transform — e.g. the ``bml_axisNode`` for skinned model meshes).  This
    matches the standard FBX convention (local control points + a mesh bind
    transform + cluster ``TransformLink`` per bone), which Blender/Maya import
    correctly.  ``skin`` holds, per vertex, ``(global_bone_index, weight)``."""
    name: str
    material_name: str
    positions: List[Vec3] = field(default_factory=list)
    normals: List[Vec3] = field(default_factory=list)
    uvs: List[Tuple[float, float]] = field(default_factory=list)
    uvs2: List[Tuple[float, float]] = field(default_factory=list)
    colors: List[Tuple[int, int, int, int]] = field(default_factory=list)
    triangles: List[Tuple[int, int, int]] = field(default_factory=list)
    bind_transform: Mat4 = field(default_factory=Mat4)
    skin: List[List[Tuple[int, float]]] = field(default_factory=list)
    morphs: List[MorphTarget] = field(default_factory=list)


@dataclass
class Model:
    name: str
    bones: List[Bone] = field(default_factory=list)
    materials: List[Material] = field(default_factory=list)
    submeshes: List[SubMesh] = field(default_factory=list)
    # Textures decoded straight out of the archive, by base name (no extension).
    embedded_textures: Dict[str, "object"] = field(default_factory=dict)
    # Names referenced by materials that must be resolved from sibling files.
    referenced_textures: List[str] = field(default_factory=list)

    def has_geometry(self) -> bool:
        return any(sm.positions for sm in self.submeshes)


@dataclass
class AnimTrack:
    bone_name: str
    # Each list holds (frame:int, value) tuples.
    translation: List[Tuple[int, Vec3]] = field(default_factory=list)
    rotation: List[Tuple[int, Quat]] = field(default_factory=list)
    scale: List[Tuple[int, Vec3]] = field(default_factory=list)


@dataclass
class Animation:
    name: str
    end_frame: int = 0
    fps: float = 60.0
    tracks: List[AnimTrack] = field(default_factory=list)

    def frame_count(self) -> int:
        return self.end_frame + 1


@dataclass
class CameraAnim:
    name: str
    end_frame: int = 0
    fps: float = 60.0
    base_pos: Vec3 = field(default_factory=lambda: Vec3(0.0, 0.0, 0.0))
    base_rot: Quat = field(default_factory=lambda: Quat())
    base_fov: float = 45.0
    translation: List[Tuple[int, Vec3]] = field(default_factory=list)
    rotation: List[Tuple[int, Quat]] = field(default_factory=list)
    fov: List[Tuple[int, float]] = field(default_factory=list)

    def frame_count(self) -> int:
        return self.end_frame + 1
