import json, csv, re

d = json.load(open('consolidated.json', encoding='utf-8'))
by_page = {int(k): v for k, v in d['by_page'].items()}
real_pages = set(d['real_pages'])
freeform = d['freeform_orphans']

def gender_of(row):
    if row.get('own_page'):
        return 'M'
    k = (row.get('kunya') or '').strip()
    if k.startswith('أبو') or k.startswith('ابو'):
        return 'M'
    if k.startswith('أم') or k.startswith('ام'):
        return 'F'
    h = (row.get('husband') or '').strip()
    if h and h != '-' and 'مات' not in h and 'توفي' not in h and 'لم ينجب' not in h:
        return 'F'
    return '?'

def mother_name(father_page, mother_idx):
    if not mother_idx:
        return ''
    fp = by_page.get(father_page)
    if not fp:
        return ''
    for w in fp.get('wives', []):
        if str(w['idx']) == str(mother_idx):
            return w['name']
    return ''

# ---------------- CSV ----------------
rows_out = []

def add_holder_row(pg):
    n = by_page[pg]
    path = n.get('path') or []
    rows_out.append({
        'id': pg,
        'name': n.get('self_name', ''),
        'full_name_chain': n.get('name_chain', ''),
        'kunya': '',
        'gender': 'M',
        'generation': len(path) + 1,
        'path': '/'.join(str(x) for x in path),
        'own_page': pg,
        'father_page': n.get('father_page') or '',
        'father_name': by_page.get(n.get('father_page'), {}).get('self_name', ''),
        'mother_name': '',
        'spouse_or_husband': '',
        'spouse_linked_page': '',
        'death_date': '',
        'wives': '; '.join(
            f"{w['idx']}:{w['name']}" + (f" [father_page={w['link']}]" if w.get('link') else '')
            for w in n.get('wives', [])
        ),
        'notes': n.get('comments', '').replace('\n', ' | '),
    })

for pg in real_pages:
    add_holder_row(pg)

for pg in real_pages:
    n = by_page[pg]
    for c in n.get('children', []):
        if c.get('synthetic'):
            continue  # already represented via add_holder_row
        gid = c['own_page'] if c.get('own_page') else f"f{pg}c{c['rank']}"
        gen = (len(n.get('path') or []) + 1) + 1 if pg != 3 else 2
        rows_out.append({
            'id': gid,
            'name': c.get('name', ''),
            'full_name_chain': '',
            'kunya': c.get('kunya', ''),
            'gender': gender_of(c),
            'generation': gen,
            'path': '',
            'own_page': c.get('own_page') or '',
            'father_page': pg,
            'father_name': n.get('self_name', ''),
            'mother_name': mother_name(pg, c.get('mother_idx')),
            'spouse_or_husband': c.get('husband', ''),
            'spouse_linked_page': c.get('husband_page') or '',
            'death_date': c.get('date', ''),
            'wives': '',
            'notes': '',
        })

for pg in freeform:
    n = by_page[pg]
    rows_out.append({
        'id': pg, 'name': n.get('freeform_text', '')[:40], 'full_name_chain': '',
        'kunya': '', 'gender': '', 'generation': '', 'path': '', 'own_page': pg,
        'father_page': '', 'father_name': '', 'mother_name': '', 'spouse_or_husband': '',
        'spouse_linked_page': '', 'death_date': '', 'wives': '',
        'notes': n.get('freeform_text', ''),
    })

cols = ['id','name','full_name_chain','kunya','gender','generation','path','own_page',
        'father_page','father_name','mother_name','spouse_or_husband','spouse_linked_page',
        'death_date','wives','notes']
with open('family_tree.csv', 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for r in rows_out:
        w.writerow(r)
print('CSV rows:', len(rows_out))

# ---------------- Hierarchical JSON ----------------
def build_row_node(pg, c, father_generation):
    node = {
        'name': c.get('name', ''),
        'kunya': c.get('kunya', ''),
        'gender': gender_of(c),
        'generation': father_generation + 1,
        'mother': mother_name(pg, c.get('mother_idx')),
        'spouse': c.get('husband', ''),
        'spouseLink': c.get('husband_page'),
        'deathDate': c.get('date', ''),
    }
    if c.get('own_page') and c['own_page'] in real_pages:
        node.update(build_holder_node(c['own_page']))
    return node

def build_holder_node(pg):
    n = by_page[pg]
    path = n.get('path') or []
    generation = len(path) + 1
    kids = [build_row_node(pg, c, generation) for c in n.get('children', [])]
    return {
        'page': pg,
        'name': n.get('self_name', ''),
        'fullChain': n.get('name_chain', ''),
        'gender': 'M',
        'path': path,
        'generation': generation,
        'wives': n.get('wives', []),
        'comments': n.get('comments', ''),
        'children': kids,
        'nameBackfilled': bool(n.get('name_backfilled')),
    }

tree = build_holder_node(3)

orphans = []
for pg in freeform:
    n = by_page[pg]
    orphans.append({'page': pg, 'text': n.get('freeform_text', '')})

json.dump({'tree': tree, 'orphans': orphans}, open('tree.json', 'w', encoding='utf-8'),
          ensure_ascii=False, separators=(',', ':'))

import os
print('tree.json size (KB):', os.path.getsize('tree.json') / 1024)
