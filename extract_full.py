import fitz, json, re, sys

PDF = '/Users/admin/Downloads/family tree 1-2026.pdf'
doc = fitz.open(PDF)
NPAGES = doc.page_count

def _fix_char_order(chars):
    # chars: rawdict 'chars' entries (each has 'origin', 'bbox', 'c') in raw
    # content-stream order. PyMuPDF's span text sometimes preserves stream
    # order instead of visual order, which scrambles RTL punctuation like
    # "(name)" into ")( name". Sorting by x (descending = RTL reading order)
    # fixes that, but two things need special handling:
    #  - a plain space glyph's own origin is occasionally mispositioned by a
    #    fraction of a point (font metric rounding), enough to sort it into
    #    the wrong slot -- so space glyphs are dropped and spaces are instead
    #    reinserted based on the gap between real "ink" glyphs.
    #  - some ligatures (e.g. the "لله" in "الله") decompose into extra
    #    zero-width characters whose own x-origin is unreliable; these are
    #    kept glued to whichever real glyph immediately preceded them in the
    #    stream instead of being independently sorted, and the gap check is
    #    skipped across that glue point (the ligature's true rendered width
    #    isn't reflected in any bbox, so it would otherwise look like a gap
    #    wide enough to be a false space).
    # Digits are drawn left-to-right even inside an RTL run, so digit runs
    # are re-reversed after the sort to keep numbers like "2024" from
    # coming out backwards.
    anchors = []
    attach_after = {}
    leading = []
    last_anchor = None
    for c in chars:
        if c['c'] == ' ':
            continue
        x0, x1 = c['bbox'][0], c['bbox'][2]
        if x1 - x0 < 0.3:
            if last_anchor is None:
                leading.append(c)
            else:
                attach_after.setdefault(id(last_anchor), []).append(c)
            continue
        anchors.append(c)
        last_anchor = c
    if not anchors:
        return ' '
    anchors_sorted = sorted(anchors, key=lambda c: -c['origin'][0])
    out = [c['c'] for c in leading]
    prev_x0 = None
    prev_had_attach = False
    for c in anchors_sorted:
        x0, x1 = c['bbox'][0], c['bbox'][2]
        if prev_x0 is not None and not prev_had_attach and (prev_x0 - x1) > 1.2:
            out.append(' ')
        out.append(c['c'])
        extra = attach_after.get(id(c), [])
        out.extend(e['c'] for e in extra)
        prev_x0 = x0
        prev_had_attach = bool(extra)
    i = 0
    while i < len(out):
        if out[i].isdigit():
            j = i
            while j < len(out) and out[j].isdigit():
                j += 1
            out[i:j] = list(reversed(out[i:j]))
            i = j
        else:
            i += 1
    return ''.join(out)

def get_spans(page):
    d = page.get_text('rawdict')
    spans = []
    for b in d['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                chars = s.get('chars', [])
                if not chars:
                    continue
                t = _fix_char_order(chars)
                if t.strip() == '':
                    continue
                x0,y0,x1,y1 = s['bbox']
                spans.append({'x0':x0,'y0':y0,'x1':x1,'y1':y1,
                              'xc':(x0+x1)/2,'yc':(y0+y1)/2,'text':t})
    return spans

def cluster_rows(spans, tol=6):
    spans = sorted(spans, key=lambda s: s['yc'])
    rows = []
    for s in spans:
        placed = False
        for r in rows:
            if abs(r['yc'] - s['yc']) <= tol:
                r['spans'].append(s)
                r['yc'] = sum(x['yc'] for x in r['spans'])/len(r['spans'])
                placed = True
                break
        if not placed:
            rows.append({'yc': s['yc'], 'spans':[s]})
    rows.sort(key=lambda r: r['yc'])
    return rows

def text_join(spans_list, rtl=True):
    ss = sorted(spans_list, key=lambda s: -s['x0'] if rtl else s['x0'])
    return re.sub(r'\s+', ' ', ' '.join(s['text'] for s in ss)).strip()

def links_for_page(page):
    out = []
    for l in page.get_links():
        if l.get('page', -1) is not None and l.get('page', -1) >= 0:
            r = l['from']
            out.append({'x0':r.x0,'y0':r.y0,'x1':r.x1,'y1':r.y1,
                        'xc':(r.x0+r.x1)/2,'yc':(r.y0+r.y1)/2,'target': l['page']+1})
    return out

PATH_RE = re.compile(r'^[0-9/\s]+$')
ARABIC_RE = re.compile(r'[؀-ۿ]')

def has_arabic(s):
    return bool(ARABIC_RE.search(s or ''))

def parse_page(pno):
    page = doc[pno-1]
    spans = get_spans(page)
    links = links_for_page(page)
    if not spans:
        return {'page': pno, 'empty': True}

    zawjat = [s for s in spans if 'الزوجات' in s['text']]
    aadad = [s for s in spans if 'العدد' in s['text']]
    ta3liqat = [s for s in spans if 'التعليقات' in s['text']]
    alt_table_hdr = [s for s in spans if 'اللقب أو الكنية' in s['text'] and 'العدد' not in s['text']]

    y_zawjat = min((s['y0'] for s in zawjat), default=None)
    y_aadad = min((s['y0'] for s in aadad), default=None)
    y_ta3 = min((s['y0'] for s in ta3liqat), default=None)
    y_table_top = y_aadad if y_aadad is not None else (min((s['y0'] for s in alt_table_hdr), default=None))

    is_root = (pno == 3)

    # ---------- HEADER (name chain + links) ----------
    header_top = 55  # below page-number
    header_bottom = 85 if is_root else (y_zawjat if y_zawjat else (y_table_top if y_table_top else 200))
    header_spans = [s for s in spans if header_top < s['y0'] < header_bottom]
    # drop path tokens, 'الجيل' / 'صورة' placeholders, lone page-number repeats
    name_spans = []
    path_spans = []
    for s in header_spans:
        t = s['text'].strip()
        if t == '':
            continue
        if PATH_RE.match(t) and s['x0'] > 400:
            path_spans.append(s)
            continue
        if 'الجيل' in t or 'صورة' in t:
            continue
        if t == str(pno):
            continue
        name_spans.append(s)

    # path: order by (line-bucket asc, x desc)
    def line_bucket(s, tol=8):
        return round(s['y0']/tol)
    path_spans_sorted = sorted(path_spans, key=lambda s: (line_bucket(s), -s['x0']))
    path_str = re.sub(r'\s+', '', ''.join(s['text'] for s in path_spans_sorted))
    path = [int(x) for x in path_str.split('/') if x.isdigit()] if path_str else []

    # name chain: order by (line-bucket asc within header rows, x desc)
    rows_ns = cluster_rows(name_spans, tol=8)
    rows_ns.sort(key=lambda r: r['yc'])
    name_chain = ' '.join(text_join(r['spans']) for r in rows_ns)
    name_chain = re.sub(r'\s+', ' ', name_chain).strip()
    self_name = name_chain.split(' بن ')[0].strip() if name_chain else ''

    # header links, in chain order -> ancestor page chain (closest ancestor first)
    if is_root:
        ancestor_chain = []
        father_page = None
    else:
        header_links = [l for l in links if header_top < l['y0'] < header_bottom and l['target'] != pno]
        buckets = sorted(set(line_bucket(l, 8) for l in header_links))
        keep_buckets = set(buckets[:2])  # self-chain wraps at most 2 lines; further blocks are narrative cross-refs
        header_links = [l for l in header_links if line_bucket(l, 8) in keep_buckets]
        header_links_sorted = sorted(header_links, key=lambda l: (line_bucket(l,8), -l['x0']))
        ancestor_chain = [l['target'] for l in header_links_sorted]
        father_page = ancestor_chain[0] if ancestor_chain else None

    if is_root:
        path = []
        self_name = 'شيخ عبدهللا (الكبير)'
        name_chain = self_name

    # ---------- WIVES ----------
    wives = []
    if is_root:
        wz = [s for s in spans if y_zawjat is not None and s['y0'] >= y_zawjat - 2 and (y_table_top is None or s['y0'] < y_table_top) and s['y0'] < 170]
        wrows = cluster_rows(wz, tol=8)
        for r in wrows:
            idx_spans = [s for s in r['spans'] if s['text'].strip() in ('1','2','3','4','5') and 405 <= s['x0'] <= 425]
            if idx_spans:
                idx = int(idx_spans[0]['text'].strip())
                rest = [s for s in r['spans'] if s is not idx_spans[0] and 'الزوجات' not in s['text'] and 'صورة' not in s['text'] and 'الجيل' not in s['text']]
                nm = text_join(rest)
                if nm and nm != '*' and has_arabic(nm) and 'صورة' not in nm and 'الجيل' not in nm:
                    row_links = sorted([l for l in links if abs(l['yc'] - r['yc']) <= 9],
                                        key=lambda l: -l['x0'])
                    wlink = row_links[0]['target'] if row_links else None
                    wives.append({'idx': idx, 'name': nm, 'link': wlink})
    elif y_zawjat is not None and y_table_top is not None:
        wz = [s for s in spans if y_zawjat - 2 <= s['y0'] < y_table_top]
        wrows = cluster_rows(wz, tol=8)
        for r in wrows:
            idx_spans = [s for s in r['spans'] if s['text'].strip() in ('1','2','3','4','5') and 448 <= s['x0'] <= 466]
            if idx_spans:
                idx = int(idx_spans[0]['text'].strip())
                rest = [s for s in r['spans'] if s is not idx_spans[0] and 'الزوجات' not in s['text'] and 'صورة' not in s['text'] and 'الجيل' not in s['text']]
                nm = text_join(rest)
                if nm and nm != '*' and has_arabic(nm) and 'صورة' not in nm and 'الجيل' not in nm:
                    row_links = sorted([l for l in links if abs(l['yc'] - r['yc']) <= 9],
                                        key=lambda l: -l['x0'])
                    wlink = row_links[0]['target'] if row_links else None
                    wives.append({'idx': idx, 'name': nm, 'link': wlink})

    # ---------- TABLE ROWS ----------
    children = []
    if y_table_top is not None:
        bottom = y_ta3 if y_ta3 else 10**9
        tz = [s for s in spans if s['y0'] > y_table_top + 5 and s['y0'] < bottom]
        trows = cluster_rows(tz, tol=6)
        tl = [l for l in links if l['y0'] > y_table_top + 5 and l['y0'] < bottom]
        for r in trows:
            rank_s = [s for s in r['spans'] if s['x0'] >= 505]
            name_s = [s for s in r['spans'] if 445 <= s['x0'] < 505]
            kunya_s = [s for s in r['spans'] if 350 <= s['x0'] < 445]
            date_s = [s for s in r['spans'] if 265 <= s['x0'] < 350]
            husb_s = [s for s in r['spans'] if 85 <= s['x0'] < 265]
            mom_s = [s for s in r['spans'] if s['x0'] < 85]
            if not rank_s:
                continue
            rank_txt = text_join(rank_s)
            if not rank_txt.isdigit():
                continue
            rank = int(rank_txt)
            name = text_join(name_s)
            kunya = text_join(kunya_s)
            date = text_join(date_s)
            husb = text_join(husb_s)
            mom = text_join(mom_s)
            if not (name or kunya or husb or mom):
                continue
            # drop decorative/blank template rows (dashes, stray years, etc.)
            if not (has_arabic(name) or has_arabic(kunya) or has_arabic(husb)):
                continue
            row_yc = r['yc']
            own_page = None
            husband_page = None
            for l in tl:
                if abs(l['yc'] - row_yc) <= 8:
                    if 445 <= l['x0'] < 505:
                        own_page = l['target']
                    elif 85 <= l['x0'] < 265:
                        husband_page = l['target']
            children.append({
                'rank': rank, 'name': name, 'kunya': kunya, 'date': date,
                'husband': husb, 'mother_idx': mom if mom.isdigit() else None,
                'own_page': own_page, 'husband_page': husband_page,
            })

    # ---------- COMMENTS ----------
    comments = ''
    if y_ta3 is not None:
        cz = [s for s in spans if s['y0'] > y_ta3 + 5]
        crows = cluster_rows(cz, tol=8)
        comments = '\n'.join(text_join(r['spans']) for r in crows)

    return {
        'page': pno,
        'path': path,
        'name_chain': name_chain,
        'self_name': self_name,
        'father_page': father_page,
        'ancestor_chain': ancestor_chain,
        'wives': wives,
        'children': children,
        'comments': comments,
    }

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'all':
        out = []
        for p in range(1, NPAGES+1):
            try:
                out.append(parse_page(p))
            except Exception as e:
                out.append({'page': p, 'error': str(e)})
        with open('nodes_raw.json', 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print('wrote', len(out), 'pages')
    else:
        pno = int(sys.argv[1])
        r = parse_page(pno)
        print(json.dumps(r, ensure_ascii=False, indent=2))
