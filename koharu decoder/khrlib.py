"""Self-contained Koharu .khrproj reader/writer. Verified against a real project."""
import os, struct, hashlib

HDR = 52
MAGIC = b'KHRSTATE'

try:
    from blake3 import blake3
except ImportError:
    raise SystemExit('pip install blake3')

# ---------------------------------------------------------------- reader

class R:
    """Cursor over revision-crate wire bytes."""
    __slots__ = ('b', 'i')
    def __init__(self, b, i=0):
        self.b = b; self.i = i

    def u8(self):
        v = self.b[self.i]; self.i += 1; return v
    def raw(self, n):
        v = self.b[self.i:self.i+n]; self.i += n; return v
    def uint(self):
        t = self.b[self.i]; self.i += 1
        if t < 251: return t
        if t == 251: v = struct.unpack_from('<H', self.b, self.i)[0]; self.i += 2; return v
        if t == 252: v = struct.unpack_from('<I', self.b, self.i)[0]; self.i += 4; return v
        if t == 253: v = struct.unpack_from('<Q', self.b, self.i)[0]; self.i += 8; return v
        raise ValueError('bad varint tag %d at %d' % (t, self.i-1))
    def f32(self): return struct.unpack('<f', self.raw(4))[0]
    def f64(self): return struct.unpack('<d', self.raw(8))[0]
    def string(self):
        n = self.uint(); return self.raw(n).decode('utf-8', 'replace') if n else ''
    def bytes(self):
        n = self.uint(); return self.raw(n) if n else b''
    def uuid(self):
        h = self.raw(16).hex()
        return '%s-%s-%s-%s-%s' % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])
    def opt(self, f): return f(self) if self.u8() else None
    def map(self):
        return {self.string(): self.string() for _ in range(self.uint())}
    def vec(self, f):
        n = self.uint(); return [f(self) for _ in range(n)]
    def ent(self):   return (self.uint(), self.uuid())
    def blob(self):  return (self.uint(), self.raw(32).hex())
    def done(self):  return self.i >= len(self.b)

# ---------------------------------------------------------------- storage layer

def read_state(path):
    """Decode the state container -> header fields, blob ids, scene payload."""
    raw = open(path, 'rb').read()
    if raw[:8] != MAGIC:
        raise ValueError('not a Koharu state file')
    version = struct.unpack_from('<I', raw, 8)[0]
    body_len = struct.unpack_from('<Q', raw, 12)[0]
    checksum = raw[20:HDR]
    body = raw[HDR:]
    if len(body) != body_len:
        raise ValueError('body length mismatch: %d != %d' % (len(body), body_len))
    if blake3(body).digest() != checksum:
        raise ValueError('checksum mismatch - file is corrupt')
    payload_off = payload_offset(body)
    return dict(raw=raw, version=version, body=body,
                document=None, blobs=None, payload=body[payload_off:],
                payload_off=payload_off)

def payload_offset(body):
    """Scene payload is the LAST field of StoredState; find it exactly."""
    p = R(body)
    p.uint()                      # StoredState schema revision
    p.uint(); p.uuid()            # DocumentId newtype + 16 bytes
    p.uint(); p.uint()            # Revision newtype + varint
    for _ in range(p.uint()):     # Vec<BlobId>, each element has its own header
        p.uint(); p.raw(32)
    n = p.uint()
    off = p.i
    if off + n != len(body):
        raise ValueError('payload is not the final field')
    return off

def newest_state(project, slots=('state-a.khr', 'state-b.khr')):
    """Koharu alternates two slots; pick the highest revision that validates."""
    best = None
    for s in slots:
        p = os.path.join(project, s)
        if not os.path.isfile(p):
            continue
        try:
            st = read_state(p)
        except Exception:
            continue
        if best is None:
            best = (st, s)
        else:
            # peek revision without full decode
            p2 = R(st['body'])
            p2.uint(); p2.uint(); p2.uuid()
            rev = p2.uint(); p2.uint()
            best_rev = best[0].get('_rev')
            if best_rev is None:
                q = R(best[0]['body']); q.uint(); q.uint(); q.uuid()
                best_rev = q.uint(); best[0]['_rev'] = best_rev
            if rev > best_rev:
                best = (st, s)
    if best is None:
        raise ValueError('no valid state file')
    return best

def read_state_full(path):
    """read_state + document id / revision / blob list."""
    st = read_state(path)
    p = R(st['body'])
    p.uint()
    p.uint(); doc = p.uuid()
    p.uint(); rev = p.uint()
    blobs = [p.blob()[1] for _ in range(p.uint())]
    st.update(document=doc, revision=rev, blobs=blobs)
    return st

# ---------------------------------------------------------------- scene layer

def origin(r):
    """Origin enum: User | Generated(Generation)"""
    r.uint()                      # enum revision header
    d = r.uint()
    if d == 0:
        return dict(origin='User')
    r.uint()                      # Generation struct revision
    r.uint()                      # ProducerId newtype revision
    gen = dict(producer=r.string(),
               model=r.opt(lambda rr: rr.string()),
               confidence=r.opt(lambda rr: rr.f32()))
    return dict(origin='Generated', **gen)

def lang(r):
    r.uint(); return r.string()   # LanguageTag newtype

def point(r):
    r.uint(); return (r.f64(), r.f64())

def _text(r):
    """SourceText / Translation have MANUAL SerializeRevisioned: no header."""
    val = r.string()
    return dict(text=val, origin=origin(r),
                language=r.opt(lambda rr: lang(rr)))

def _asset(r):
    r.uint()                                  # Asset revision
    o = origin(r)
    blob = r.blob()                           # BlobId newtype + 32 bytes
    media = r.string()
    r.uint()                                  # AssetMetadata revision
    w = r.opt(lambda rr: rr.uint()); h = r.opt(lambda rr: rr.uint())
    return dict(origin=o, blob=blob[1], media_type=media,
                width=w, height=h, attributes=r.map())

def _region(r):
    r.uint(); o = origin(r)
    r.uint(); k = r.string()                  # RegionKind newtype
    return dict(origin=o, kind=k, label=r.opt(lambda rr: rr.string()))

def _geometry(r):
    r.uint(); o = origin(r)
    pts = r.vec(lambda rr: point(rr))
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    bbox = [min(xs), min(ys), max(xs), max(ys)] if xs else None
    return dict(origin=o, points=pts, bbox=bbox)

def _ocr(r):
    r.uint(); o = origin(r)
    r.uint(); d = r.uint()                    # TextDirection enum: rev + discriminant
    conf = r.opt(lambda rr: rr.f32())
    lines = []
    for _ in range(r.uint()):
        lines.append([point(r) for _ in range(4)])
    return dict(origin=o, direction=d, confidence=conf, lines=lines)

def _typography(r):
    r.uint(); o = origin(r)
    font = r.opt(lambda rr: rr.string())
    weight = r.opt(lambda rr: rr.uint())
    style = r.opt(lambda rr: (rr.uint(), rr.uint()))    # FontStyle enum
    size = r.opt(lambda rr: rr.f32())
    auto_fit = bool(r.u8())
    color = r.opt(lambda rr: rr.raw(4).hex())           # Option<[u8;4]>
    stroke_color = r.opt(lambda rr: rr.raw(4).hex())
    stroke_width = r.opt(lambda rr: rr.f32())
    align = r.opt(lambda rr: (rr.uint(), rr.uint()))
    mode = r.opt(lambda rr: (rr.uint(), rr.uint()))
    return dict(origin=o, preferred_font=font, font_weight=weight,
                font_style=style, size=size, auto_fit=auto_fit,
                color=color, stroke_color=stroke_color,
                stroke_width=stroke_width, alignment=align,
                writing_mode=mode, extensions=r.map())

def _text_layout(r):
    r.uint(); o = origin(r)                   # TextLayout is revision = 2
    r.uint(); k = r.uint()                    # TextLayoutKind enum
    return dict(origin=o, kind=k,
                angle_degrees=r.opt(lambda rr: rr.f32()))

def _detection(r):
    r.uint(); o = origin(r)
    labels = []
    for _ in range(r.uint()):
        r.uint()                              # DetectionLabel revision
        r.uint(); k = r.string()              # RegionKind newtype
        labels.append((k, r.f32()))
    return dict(origin=o, labels=labels)

def _text_role(r):
    r.uint(); o = origin(r)
    return dict(origin=o, role=r.string())

def _page(r):
    r.uint()
    return dict(label=r.string(), width=r.f64(), height=r.f64())

def _group(r):
    r.uint(); o = origin(r)
    return dict(origin=o, name=r.string())

def _visibility(r):
    r.uint(); o = origin(r)
    return dict(origin=o, visible=bool(r.u8()), opacity=r.f32())

def _raster_layer(r):
    r.uint(); o = origin(r)
    name = r.string()
    r.uint(); k = r.uint()                    # RasterLayerKind enum
    return dict(origin=o, name=name, kind=k)

def _text_content(r):
    r.uint()
    return origin(r)

def _text_group(r):
    r.uint()
    return dict(kind='TextGroup')

def _entity_origin(r):
    r.uint()
    return origin(r)

def _assets(r):
    r.uint()                                  # Assets struct revision
    out = {}
    for _ in range(r.uint()):
        r.uint(); role = r.string()           # AssetRole newtype
        out[role] = _asset(r)
    return out

DECODERS = {
    'dev.koharu.text.source': _text,
    'dev.koharu.text.translation': _text,
    'dev.koharu.text.content': _text_content,
    'dev.koharu.text.role': _text_role,
    'dev.koharu.text.group': _text_group,
    'dev.koharu.text.typography': _typography,
    'dev.koharu.entity.origin': _entity_origin,
    'dev.koharu.geometry': _geometry,
    'dev.koharu.analysis.region': _region,
    'dev.koharu.analysis.ocr': _ocr,
    'dev.koharu.analysis.detection': _detection,
    'dev.koharu.layer.text': _text_layout,
    'dev.koharu.layer.raster': _raster_layer,
    'dev.koharu.visibility': _visibility,
    'dev.koharu.page': _page,
    'dev.koharu.group': _group,
    'dev.koharu.assets': _assets,
}

def component(r):
    """StoredComponentEntry { ComponentKey, StoredComponent }"""
    r.uint()                                  # entry revision
    r.uint(); kind = r.string()               # ComponentKey newtype + String
    r.uint()                                  # StoredComponent revision
    schema = r.uint()
    payload = r.bytes()
    refs = [r.ent()[1] for _ in range(r.uint())]
    blobs = [r.blob()[1] for _ in range(r.uint())]
    dec = DECODERS.get(kind)
    val = None
    if dec:
        try:
            val = dec(R(payload))
        except Exception as ex:
            val = dict(error=str(ex))
    return dict(kind=kind, schema=schema, payload=payload,
                record_refs=refs, blob_refs=blobs, value=val)

def scene(payload):
    """koharu_scene::state::StoredState"""
    r = R(payload)
    r.uint()                                  # scene revision
    epoch = r.uint()                          # page_order_epoch
    project = [component(r) for _ in range(r.uint())]
    pages = []
    for _ in range(r.uint()):
        r.uint(); r.uint(); pid = r.uuid(); ep = r.uint()
        ents = []
        for _ in range(r.uint()):
            r.uint(); r.uint(); eid = r.uuid()
            parent = r.opt(lambda rr: rr.ent())
            ents.append(dict(id=eid, parent=parent[1] if parent else None,
                             components=[component(r) for _ in range(r.uint())]))
        pages.append(dict(id=pid, epoch=ep, entities=ents))
    rels = []
    for _ in range(r.uint()):
        r.uint(); r.uint(); rid = r.uuid()     # StoredRelation, RelationId
        r.uint(); o = origin(r)               # Relation struct revision
        r.uint(); kind = r.string()           # RelationKind newtype
        src = r.ent(); tgt = r.ent()
        rels.append(dict(id=rid, value=dict(origin=o, kind=kind,
                                            source=src[1], target=tgt[1]),
                         components=[component(r) for _ in range(r.uint())]))
    return dict(page_order_epoch=epoch, project=project, pages=pages,
                relations=rels, consumed=r.i, total=len(payload))

# ---------------------------------------------------------------- higher level

def load(project, slot=None):
    """slot=None -> newest valid slot."""
    if slot:
        st = read_state_full(os.path.join(project, slot)); name = slot
    else:
        st, name = newest_state(project)
        st = read_state_full(os.path.join(project, name))
    sc = scene(st['payload'])
    return dict(slot=name, container=st, scene=sc)

def text_regions(sc):
    """Resolve the 3-layer graph into flat text rows.

    text entity --recognized-from--> region entity (geometry, ocr)
    layout entity --presents-->     text entity (typography)
    """
    rel_out, rel_in = {}, {}
    for rel in sc['relations']:
        v = rel['value']
        rel_out.setdefault(v['source'], []).append((v['kind'], v['target']))
        rel_in.setdefault(v['target'], []).append((v['kind'], v['source']))

    rows = []
    for pi, pg in enumerate(sc['pages']):
        by_id = {e['id']: {c['kind']: c['value'] for c in e['components']}
                 for e in pg['entities']}
        for e in pg['entities']:
            c = by_id[e['id']]
            src = c.get('dev.koharu.text.source')
            tr = c.get('dev.koharu.text.translation')
            if src is None and tr is None:
                continue
            region_id = next((t for k, t in rel_out.get(e['id'], [])
                              if k == 'dev.koharu.relation.recognized-from'), None)
            layout_id = next((s for k, s in rel_in.get(e['id'], [])
                              if k == 'dev.koharu.relation.presents'), None)
            rc = by_id.get(region_id) or {}
            lc = by_id.get(layout_id) or {}
            rows.append(dict(
                page=pi, id=e['id'], region_id=region_id, layout_id=layout_id,
                bbox=rc.get('dev.koharu.geometry', {}).get('bbox'),
                region=rc.get('dev.koharu.analysis.region', {}).get('kind'),
                role=c.get('dev.koharu.text.role', {}).get('role'),
                ocr_conf=rc.get('dev.koharu.analysis.ocr', {}).get('confidence'),
                src=src.get('text') if src else None,
                src_lang=src.get('language') if src else None,
                tr=tr.get('text') if tr else None,
                tr_lang=tr.get('language') if tr else None,
                color=lc.get('dev.koharu.text.typography', {}).get('color'),
                font=lc.get('dev.koharu.text.typography', {}).get('preferred_font'),
                size=lc.get('dev.koharu.text.typography', {}).get('size')))
    return rows

def blob_path(project, blob_id):
    return os.path.join(project, 'blobs', blob_id[:2], blob_id)

def page_assets(sc):
    """page -> {role: {blob, media_type, width, height}} from the root entity."""
    out = []
    for pg in sc['pages']:
        c = {x['kind']: x['value'] for x in pg['entities'][0]['components']}
        info = c.get('dev.koharu.page') or {}
        assets = c.get('dev.koharu.assets') or {}
        out.append(dict(label=info.get('label'), width=info.get('width'),
                        height=info.get('height'), assets=assets))
    return out

# ---------------------------------------------------------------- write path

def _walk_typography_color(sub, base, hits):
    """Consume a Typography struct, recording the absolute offset of .color."""
    r = R(sub)
    r.uint()                                  # Typography struct revision
    origin(r)
    r.opt(lambda rr: rr.string())             # preferred_font
    r.opt(lambda rr: rr.uint())               # font_weight
    r.opt(lambda rr: (rr.uint(), rr.uint()))  # font_style
    r.opt(lambda rr: rr.f32())                # size
    r.u8()                                    # auto_fit
    if r.u8():                                # Option tag for color
        hits.append((base + r.i, r.raw(4)))
    else:
        hits.append((None, None))
    r.opt(lambda rr: rr.raw(4))               # stroke_color
    r.opt(lambda rr: rr.f32())                # stroke_width
    r.opt(lambda rr: (rr.uint(), rr.uint()))  # alignment
    r.opt(lambda rr: (rr.uint(), rr.uint()))  # writing_mode
    r.map()                                   # extensions
    if r.i != len(sub):
        raise ValueError('typography walk overran payload %d/%d' % (r.i, len(sub)))


def find_typography_colors(state):
    """-> [(abs_file_offset, b'RRGGBBAA'), ...] for every Typography component.

    Offsets are absolute within the state file, so they can be used directly.
    """
    payload = state['payload']
    base = HDR + state['payload_off']
    hits = []

    def skip_component(rr):
        rr.uint()                              # StoredComponentEntry revision
        rr.uint(); kind = rr.string()          # ComponentKey newtype + String
        rr.uint(); rr.uint()                   # StoredComponent revision + schema
        plen = rr.uint()
        pstart = rr.i
        if kind == 'dev.koharu.text.typography':
            _walk_typography_color(payload[pstart:pstart + plen],
                                   base + pstart, hits)
        rr.i = pstart + plen
        for _ in range(rr.uint()):
            rr.uint(); rr.raw(16)              # record_refs
        for _ in range(rr.uint()):
            rr.uint(); rr.raw(32)              # blob_refs

    r = R(payload)
    r.uint(); r.uint()                         # revision, page_order_epoch
    for _ in range(r.uint()):
        skip_component(r)
    for _ in range(r.uint()):                  # pages
        r.uint(); r.uint(); r.uuid(); r.uint()
        for _ in range(r.uint()):              # entities
            r.uint(); r.uint(); r.uuid()
            r.opt(lambda rr: rr.ent())
            for _ in range(r.uint()):
                skip_component(r)
    for _ in range(r.uint()):                  # relations
        r.uint()                               # StoredRelation revision
        r.uint(); r.uuid()                     # RelationId newtype + 16 bytes
        r.uint()                               # Relation struct revision
        origin(r)
        r.uint(); r.string()                   # RelationKind newtype
        r.ent(); r.ent()                       # source, target
        for _ in range(r.uint()):
            skip_component(r)
    if r.i != len(payload):
        raise ValueError('scene walk incomplete %d/%d' % (r.i, len(payload)))
    return hits


def set_typography_color(state_path, rgba=b'\xff\x00\x00\xff', verify=True):
    """Rewrite every Typography.color. Returns (changed, total).

    Safe because color is a fixed 4-byte field: no lengths move, so the body
    length is untouched and only the BLAKE3 header digest needs recomputing.
    """
    st = read_state(state_path)
    if verify:
        decoded = [c['value'].get('color') for c in _typography_iter(st['payload'])]
        walked = [None if v is None else v.hex()
                  for (_, v) in find_typography_colors(st)]
        expect = [None if d is None else d for d in decoded]
        if walked != expect:
            raise AssertionError('colour walk disagrees with decoder; refusing to write')
    raw = bytearray(st['raw'])
    changed = 0
    for off, val in find_typography_colors(st):
        if off is None or val == rgba:
            continue
        if raw[off:off + 4] != val:
            raise AssertionError('offset %d holds %s, expected %s'
                                 % (off, bytes(raw[off:off + 4]).hex(), val.hex()))
        raw[off:off + 4] = rgba
        changed += 1
    body = bytes(raw[HDR:])
    if len(body) != len(st['body']):
        raise AssertionError('patch altered body length')
    raw[20:HDR] = blake3(body).digest()
    return bytes(raw), changed, len(find_typography_colors(st))


def _typography_iter(payload):
    for pg in scene(payload)['pages']:
        for e in pg['entities']:
            for c in e['components']:
                if c['kind'] == 'dev.koharu.text.typography':
                    yield c


def write_state(path, data, backup=True):
    import shutil
    if backup and os.path.isfile(path):
        shutil.copy2(path, path + '.bak')
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)