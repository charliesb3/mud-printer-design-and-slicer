"""
CANONICAL WALLS for tests that build a PrintLayer directly (the build format
walls.compose produces from per-wall envelopes): a thick wall is a Wall System
RECORD listing it — `thick(...)` — never a per-path WallSpec / Network Wall
(those legacy forms were removed from production in the 2026-10-16 cleanup;
project files still holding them are converted by walls.migrate_project).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from model import WallSystem   # noqa: E402


def thick(members, thickness, align='auto', print_reference=False, sid=None, type='hollow',
          params=None, web=None, name=''):
    """A build record giving every listed wall this envelope. type 'hollow'
    (two skins — what compose gives an unassigned thick wall), or a
    construction with its params / web."""
    members = [members] if isinstance(members, str) else list(members)
    return WallSystem(sid or f'wall:{members[0]}', type, dict(params or {}), members, name=name,
                      thickness=float(thickness), align=align, print_reference=bool(print_reference),
                      web=dict(web or {}))


def system(members, thickness, spec, align='auto', sid='WS', **kw):
    """A record of the construction `spec` ({'type': …, params…})."""
    return thick(members, thickness, align=align, sid=sid, type=spec['type'],
                 params={k: v for k, v in spec.items() if k != 'type'}, **kw)


def envelope(thickness, align='auto', print_reference=False) -> dict:
    """A per-wall `envelope` field for document dicts."""
    return {'thickness': thickness, 'align': align, 'print_reference': bool(print_reference)}


def layer(doc, systems=()):
    """The PrintLayer of an AUTHORED document (per-wall envelopes + the
    project's Wall Systems), composed exactly as /api/effective_paths does.
    (app._deserialise_layer alone reads the build format.)"""
    from app import _deserialise_layer
    from walls import compose
    return _deserialise_layer(compose(doc, None, list(systems)))
