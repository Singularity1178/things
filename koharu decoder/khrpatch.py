#!/usr/bin/env python3
"""Edit Typography colour in a Koharu .khrproj.

    python khrpatch.py <project.khrproj>                 # dry run
    python khrpatch.py <project.khrproj> --apply        # write in place
    python khrpatch.py <project.khrproj> --apply --rgb ff0000
    python khrpatch.py <project.khrproj> --apply --slot state-a.khr --all-slots

Dry run is the default. --apply backs up each touched slot to <name>.bak first.
"""
import os, sys, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import khrlib as K


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('project')
    ap.add_argument('--apply', action='store_true',
                    help='write in place (default: dry run)')
    ap.add_argument('--rgb', default='ff0000',
                    help='RRGGBB hex, alpha forced to ff (default ff0000)')
    ap.add_argument('--slot', default=None, help='default: newest valid slot')
    ap.add_argument('--all-slots', action='store_true',
                    help='patch both state-a and state-b so the change sticks '
                         'regardless of which slot Koharu loads')
    args = ap.parse_args()

    hexs = args.rgb.strip().lstrip('#')
    if len(hexs) not in (3, 6):
        sys.exit('bad --rgb: want 3 or 6 hex digits, got %r' % args.rgb)
    try:
        if len(hexs) == 3:                     # 'f0a' -> 'ff00aa'
            hexs = ''.join(c * 2 for c in hexs)
        rgb = bytes.fromhex(hexs)
    except ValueError:
        sys.exit('bad --rgb: not hex, got %r' % args.rgb)
    rgba = rgb + b'\xff'

    slots = (['state-a.khr', 'state-b.khr'] if args.all_slots
             else [args.slot] if args.slot
             else [K.newest_state(args.project)[1]])

    for name in slots:
        path = os.path.join(args.project, name)
        if not os.path.isfile(path):
            print('%s: not present, skipped' % name)
            continue
        data, changed, total = K.set_typography_color(path, rgba)
        unset = sum(1 for _, v in K.find_typography_colors(K.read_state(path))
                    if v is None)
        print('%s  %d/%d recoloured%s'
              % (name, changed, total,
                 '  (%d regions have no colour set -> Koharu default, '
                 'cannot be set by patching)' % unset if unset else ''))
        if args.apply:
            K.write_state(path, data)
            print('        written, backup at %s.bak' % name)

    if not args.apply:
        print('\ndry run - nothing written. add --apply to write.')


if __name__ == '__main__':
    main()