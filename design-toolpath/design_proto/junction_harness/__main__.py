"""Command-line entry point.

Usage (from `design-toolpath/`):

    design_proto/.venv/bin/python -m design_proto.junction_harness
        # run every registered fixture; write SVGs to design_proto/junction_harness/_out/

    design_proto/.venv/bin/python -m design_proto.junction_harness --list
        # print the fixture catalogue and exit

    design_proto/.venv/bin/python -m design_proto.junction_harness single_to_thick
        # run just one fixture (by name)

    design_proto/.venv/bin/python -m design_proto.junction_harness --style round --system interleaved
        # override the default junction style / Wall System for every fixture

    design_proto/.venv/bin/python -m design_proto.junction_harness --out /tmp/jh
        # write SVGs to an alternate directory
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from . import fixtures as F
from . import runner as R


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m design_proto.junction_harness')
    ap.add_argument('fixture', nargs='*',
                    help='one or more fixture names; default: every fixture')
    ap.add_argument('--list', action='store_true', help='list fixtures and exit')
    ap.add_argument('--style', choices=['miter', 'round'],
                    help='override junction style (default: fixture default)')
    ap.add_argument('--radius', type=float,
                    help='override junction radius for round style')
    ap.add_argument('--system',
                    choices=['single', 'hollow', 'skin_web', 'parallel',
                             'interleaved', 'linked', 'chain'],
                    help='override Wall System for every single-layer fixture')
    ap.add_argument('--thick', type=float, help='override wall thickness')
    ap.add_argument('--out', type=Path, default=None,
                    help='output directory for SVGs (default: _out/ in the package)')
    ap.add_argument('--no-svg', action='store_true', help='skip SVG writing')
    ap.add_argument('--json', action='store_true', help='emit the full report as JSON')
    args = ap.parse_args(argv)

    if args.list:
        print('# junction_harness fixtures')
        for cat, name in F.catalogue():
            print(f'  {cat:6} {name}')
        return 0

    params = {}
    if args.style:
        params['style'] = args.style
    if args.radius is not None:
        params['radius'] = args.radius
    if args.system:
        params['system'] = args.system
    if args.thick is not None:
        params['thick'] = args.thick

    single, multi = [], []
    for name in args.fixture or []:
        if name in F.SINGLE_LAYER:
            single.append(name)
        elif name in F.MULTI_LAYER:
            multi.append(name)
        else:
            print(f'unknown fixture: {name!r} (see --list)', file=sys.stderr)
            return 2

    reports = R.run_catalogue(single_filter=single or None,
                              multi_filter=multi or None,
                              **params)
    # write SVGs through `run_catalogue`'s default path; honor --out via
    # a quick second pass if the user specified it (keeps the runner's
    # signature straightforward)
    if args.out and args.out != R._default_out_dir():
        args.out.mkdir(parents=True, exist_ok=True)
        # move produced SVGs
        for rep in reports:
            if rep['kind'] == 'single' and rep.get('svg'):
                src = Path(rep['svg'])
                dst = args.out / src.name
                dst.write_bytes(src.read_bytes())
                rep['svg'] = str(dst)
            elif rep['kind'] == 'multi':
                for L in rep['layers']:
                    if L.get('svg'):
                        src = Path(L['svg'])
                        dst = args.out / src.name
                        dst.write_bytes(src.read_bytes())
                        L['svg'] = str(dst)

    if args.json:
        print(json.dumps(reports, indent=2, default=str))
    else:
        print(R.summarise(reports))
        out = R._default_out_dir() if not args.out else args.out
        print(f'\nSVG previews: {out}')
        for rep in reports:
            if rep['kind'] == 'single':
                fails = [k for k, v in rep['checks'].items() if not v['ok']]
                if fails:
                    print(f'  {rep["name"]}: FAIL {fails}')
            else:
                for L in rep['layers']:
                    fails = [k for k, v in L['checks'].items() if not v['ok']]
                    if fails:
                        print(f'  {rep["name"]} L{L["layer"]:02d}: FAIL {fails}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
