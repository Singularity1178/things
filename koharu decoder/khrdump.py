#!/usr/bin/env python3
"""Export a Koharu .khrproj to readable text + images.

    python khrdump.py <project.khrproj> [-o outdir] [--slot state-a.khr]

Writes:
    <outdir>/project.json     full structured dump
    <outdir>/images/          page scans + text masks, renamed by page label
    <outdir>/text/NNNN.txt    per page, source + translation
    <outdir>/translation.txt  whole script, side by side
"""
import os, sys, json, shutil, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import khrlib as K


def extension(path):
    with open(path, 'rb') as f:
        head = f.read(8)
    if head[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    if head[:2] == b'\xff\xd8':
        return '.jpg'
    if head[:4] == b'RIFF':
        return '.wav'
    if head[:4] == b'webp':
        return '.webp'
    return '.bin'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('project')
    ap.add_argument('-o', '--out', default='koharu-out')
    ap.add_argument('--slot', default=None,
                    help='state-a.khr / state-b.khr (default: newest valid)')
    ap.add_argument('--no-images', action='store_true')
    args = ap.parse_args()

    d = K.load(args.project, args.slot)
    sc, c = d['scene'], d['container']
    rows = K.text_regions(sc)
    assets = K.page_assets(sc)

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    # reading order: manga is right-to-left -> descending x, then y
    by_page = {}
    for r in rows:
        by_page.setdefault(r['page'], []).append(r)
    for lst in by_page.values():
        lst.sort(key=lambda r: (-(r['bbox'][2] if r['bbox'] else 0),
                               r['bbox'][1] if r['bbox'] else 0))

    # ---- images
    manifest, missing = [], 0
    if not args.no_images:
        imgdir = os.path.join(out, 'images')
        os.makedirs(imgdir, exist_ok=True)
        for i, pa in enumerate(assets):
            stem = os.path.splitext(pa['label'] or ('page%02d' % i))[0]
            for role, a in pa['assets'].items():
                src = K.blob_path(args.project, a['blob'])
                if not os.path.isfile(src):
                    missing += 1
                    manifest.append(dict(page=i, role=role, blob=a['blob'],
                                         path=None))
                    continue
                dst = os.path.join(imgdir, '%s.%s%s' % (stem, role, extension(src)))
                shutil.copy2(src, dst)
                manifest.append(dict(page=i, role=role, blob=a['blob'],
                                     path=dst, bytes=os.path.getsize(src)))
        json.dump(manifest, open(os.path.join(imgdir, 'manifest.json'), 'w',
                                 encoding='utf-8'), ensure_ascii=False, indent=1)

    # ---- text
    txtdir = os.path.join(out, 'text')
    os.makedirs(txtdir, exist_ok=True)
    whole = []
    for i, pa in enumerate(assets):
        lst = by_page.get(i, [])
        head = '=== page %d  %s  (%sx%s) ===' % (
            i, pa['label'], pa['width'], pa['height'])
        page = [head]
        whole.append('### page %d - %s  [%d regions, %d translated]' % (
            i, pa['label'], len(lst), sum(1 for r in lst if r['tr'])))
        for r in lst:
            tag = (r['role'] or '').split('.')[-1]
            page.append('[%s] %s' % (tag, r['src']))
            page.append('  -> %s' % r['tr'])
            whole.append('%-10s %s' % (tag, r['src']))
            whole.append('%-10s EN: %s' % ('', r['tr']))
        whole.append('')
        stem = os.path.splitext(pa['label'] or ('page%02d' % i))[0]
        open(os.path.join(txtdir, stem + '.txt'), 'w',
             encoding='utf-8').write('\n'.join(page) + '\n')
    open(os.path.join(out, 'translation.txt'), 'w',
         encoding='utf-8').write('\n'.join(whole))

    # ---- json
    dump = dict(
        document=c['document'], slot=d['slot'], revision=c['revision'],
        page_order_epoch=sc['page_order_epoch'],
        declared_blobs=len(c['blobs']),
        parse=dict(consumed=sc['consumed'], total=sc['total'],
                   exact=sc['consumed'] == sc['total']),
        pages=[dict(index=i, label=a['label'], width=a['width'],
                    height=a['height'],
                    text_count=len(by_page.get(i, [])),
                    assets={k: v['blob'] for k, v in a['assets'].items()})
               for i, a in enumerate(assets)],
        lines=rows,
        relations=[r['value'] for r in sc['relations']],
    )
    json.dump(dump, open(os.path.join(out, 'project.json'), 'w',
                         encoding='utf-8'), ensure_ascii=False, indent=1)

    print('slot          : %s (revision %d)' % (d['slot'], c['revision']))
    print('exact parse   : %s (%d/%d bytes)'
          % (dump['parse']['exact'], sc['consumed'], sc['total']))
    print('pages         : %d' % len(assets))
    print('text regions  : %d (%d translated)'
          % (len(rows), sum(1 for r in rows if r['tr'])))
    print('relations     : %d' % len(sc['relations']))
    print('blobs         : %d declared%s'
          % (len(c['blobs']), ', %d missing' % missing if missing else ''))
    print('output        : %s' % out)


if __name__ == '__main__':
    main()