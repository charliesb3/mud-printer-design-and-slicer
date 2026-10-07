"""
LAYER ASSEMBLY — stacks reusable 2D Layer Designs vertically.

Deliberately "dumb": it knows Layer Design ids / names / derivation, one
global layer height, ordered sections given by PHYSICAL HEIGHT, and the
resulting layer instances and their Z. It does not know how walls, trims,
lattices, beads or routes work — it consumes resolved 2D printable geometry
through the LayerSource protocol (source.py). The only module that imports
the Designer is designer_source.py.
"""
from .model import (Assembly, Section, LayerInstance, SectionResult, ResolvedAssembly,
                    AssemblyError, resolve, SectionTransform, InstanceTransform, Footprint,
                    PROFILES)
from .source import LayerSource, DesignInfo, LayerGeometry, StaticSource

__all__ = ['Assembly', 'Section', 'LayerInstance', 'SectionResult', 'ResolvedAssembly',
           'AssemblyError', 'resolve', 'LayerSource', 'DesignInfo', 'LayerGeometry',
           'StaticSource', 'SectionTransform', 'InstanceTransform', 'Footprint', 'PROFILES']
