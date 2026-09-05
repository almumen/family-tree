import json, re, csv, sys
import fitz

doc = fitz.open('/Users/admin/Downloads/family tree 1-2026.pdf')
data = json.load(open('nodes_raw.json', encoding='utf-8'))
by_page = {d['page']: d for d in data}

def plain_text_len(pno):
    return len(doc[pno-1].get_text().strip())

# ---------- Step 1: classify empty-name pages ----------
empties = [d['page'] for d in data if not d.get('name_chain')]
referenced_by = {}
for d in data:
    fp = d.get('father_page')
    if fp:
        referenced_by.setdefault(fp, []).append(d['page'])

drop_pages = set()
merge_into = {}   # page -> target page to merge into
freeform_orphans = []

for pg in empties:
    d = by_page[pg]
    has_content = bool(d.get('wives')) or bool(d.get('children'))
    refs = referenced_by.get(pg, [])
    if refs:
        continue  # standalone, will backfill name below
    if has_content:
        # continuation of nearest preceding real node
        p = pg - 1
        while p >= 3 and not by_page.get(p, {}).get('name_chain') and p not in merge_into:
            if by_page.get(p, {}).get('name_chain'):
                break
            p -= 1
        # walk further through chains of merges
        target = pg - 1
        while target in merge_into:
            target = merge_into[target]
        if by_page.get(target, {}).get('name_chain'):
            merge_into[pg] = target
        else:
            drop_pages.add(pg)
    else:
        txt = doc[pg-1].get_text()
        has_table = ('العدد' in txt) or ('اللقب أو الكنية' in txt)
        if has_table:
            drop_pages.add(pg)  # blank reserved template page
        elif len(txt.strip()) > 80:
            freeform_orphans.append(pg)
        else:
            drop_pages.add(pg)

# reclassify: named pages with no table structure at all (freeform biography, or the cover page)
for d in data:
    pg = d['page']
    if pg in drop_pages or pg in merge_into:
        continue
    if d.get('name_chain') and not d.get('children') and not d.get('wives'):
        txt = doc[pg-1].get_text()
        has_table = ('العدد' in txt) or ('اللقب أو الكنية' in txt)
        if not has_table:
            if pg == 1:
                drop_pages.add(pg)
            else:
                freeform_orphans.append(pg)

print('merge_into:', merge_into)
print('drop_pages:', sorted(drop_pages))
print('freeform_orphans:', freeform_orphans)
print('standalone-but-nameless (backfill needed):', [pg for pg in empties if referenced_by.get(pg)])

# ---------- Step 2: apply merges ----------
for src, tgt in merge_into.items():
    s = by_page[src]
    t = by_page[tgt]
    base_rank = max([c['rank'] for c in t['children']], default=0)
    for c in s.get('children', []):
        c2 = dict(c)
        c2['rank'] = base_rank + c['rank']
        t['children'].append(c2)
    for w in s.get('wives', []):
        if has_arabic := re.search(r'[؀-ۿ]', w['name'] or ''):
            existing_names = {x['name'] for x in t['wives']}
            if w['name'] not in existing_names:
                next_idx = max([x['idx'] for x in t['wives']], default=0) + 1
                t['wives'].append({'idx': next_idx, 'name': w['name']})
    t['comments'] = (t.get('comments','') + '\n' + s.get('comments','')).strip()

# ---------- Step 3: backfill names for referenced-but-nameless pages ----------
for pg in empties:
    if pg in merge_into or pg in drop_pages or pg in freeform_orphans:
        continue
    d = by_page[pg]
    if d.get('name_chain'):
        continue
    refs = referenced_by.get(pg, [])
    if refs:
        child = by_page[refs[0]]
        chain = child['name_chain']
        parts = chain.split(' بن ', 1)
        if len(parts) == 2:
            d['name_chain'] = parts[1]
            d['self_name'] = parts[1].split(' بن ')[0].strip()
            d['name_backfilled'] = True
        if not d.get('path') and child.get('path'):
            d['path'] = child['path'][:-1]
            d['path_backfilled'] = True

# ---------- Step 4: build path_map & resolve father_page fallback ----------
for pg in freeform_orphans:
    by_page[pg]['freeform_text'] = re.sub(r'\s+', ' ', doc[pg-1].get_text()).strip()

real_pages = [pg for pg in by_page if by_page[pg].get('name_chain') and pg not in merge_into
              and pg not in drop_pages and pg not in freeform_orphans]
path_map = {}
for pg in real_pages:
    d = by_page[pg]
    path = d.get('path')
    if pg == 3:
        path_map[()] = 3
    elif path:
        path_map[tuple(path)] = pg

unresolved = []
for pg in real_pages:
    if pg == 3:
        continue
    d = by_page[pg]
    if d.get('father_page'):
        continue
    path = d.get('path')
    if path:
        parent = path_map.get(tuple(path[:-1]))
        if parent:
            d['father_page'] = parent
            d['father_via'] = 'path'
            continue
    unresolved.append(pg)

print('still unresolved father_page:', unresolved)

# ---------- Step 5: build children map (father_page -> list of pages) ----------
kids_map = {}
for pg in real_pages:
    if pg == 3:
        continue
    d = by_page[pg]
    fp = d.get('father_page')
    if fp:
        kids_map.setdefault(fp, []).append(pg)

# For each father, merge table-row data with kids_map to ensure completeness
for pg in real_pages:
    d = by_page[pg]
    rows = d.get('children', [])
    row_own_pages = {c['own_page'] for c in rows if c.get('own_page')}
    extra_kids = [k for k in kids_map.get(pg, []) if k not in row_own_pages]
    for k in extra_kids:
        kd = by_page[k]
        base_rank = max([c['rank'] for c in rows], default=0)
        rows.append({
            'rank': base_rank + 1, 'name': kd.get('self_name',''), 'kunya': '',
            'date': '', 'husband': '', 'mother_idx': None,
            'own_page': k, 'husband_page': None, 'synthetic': True,
        })

json.dump({'by_page': by_page, 'real_pages': real_pages, 'freeform_orphans': freeform_orphans,
           'drop_pages': sorted(drop_pages), 'merge_into': merge_into},
          open('consolidated.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('done, real_pages:', len(real_pages))
