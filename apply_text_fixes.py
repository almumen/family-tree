import json, re

d = json.load(open('consolidated.json', encoding='utf-8'))
by_page = d['by_page']

# ---- 1. spelling normalization ----
def normalize_allah(s):
    s = s.replace('عبد هللا', 'عبدالله')
    s = s.replace('هللا', 'الله')
    return s

abdullah_variants = [
    'ع بد الله', 'ع د الله', 'عب د الله', 'بع د الله',
    'ع بد هللا', 'ع د هللا', 'عب د هللا', 'بع د هللا',
]

# ---- 2. title-prefix + word-split fixes ----
p_bin      = re.compile(r'(?<![؀-ۿ])ب\s+ن(?![؀-ۿ])')
p_slash_bn = re.compile(r'/\s*بن\b')
p_dr_fwd   = re.compile(r'/\s*د\b\s*')
p_dr_rev   = re.compile(r'\bد\s*/\s*')
p_sh_fwd   = re.compile(r'/\s*ش\b\s*')
p_sh_rev   = re.compile(r'\bش\s*/\s*')
p_lead_sl  = re.compile(r'^/(?=[؀-ۿ])')
p_hasan    = re.compile(r'حس\s+ن')

# ---- 3. lam-alef ligature bug: confirmed-safe whole-word fixes ----
lam_alef_fixes = [
    (re.compile(r'(?<![؀-ۿ])صلح(?![؀-ۿ])'), 'صلاح'),
    (re.compile(r'(?<![؀-ۿ])هلل(?![؀-ۿ])'), 'هلال'),
    (re.compile(r'(?<![؀-ۿ])جلل(?![؀-ۿ])'), 'جلال'),
    (re.compile(r'(?<![؀-ۿ])طلل(?![؀-ۿ])'), 'طلال'),
    (re.compile(r'(?<![؀-ۿ])طالل(?![؀-ۿ])'), 'طلال'),
    (re.compile(r'إصلح(?![؀-ۿ])'), 'إصلاح'),   # "Islah" with the ligature-alef restored
    (re.compile(r'الهلل(?![؀-ۿ])'), 'الهلال'), # "Al-Hilal" likewise
    (re.compile(r'(?<![؀-ۿ])المل(?![؀-ۿ])'), 'الملا'),  # "Al-Mulla" (title/surname)
    (re.compile(r'(?<![؀-ۿ])مل(?![؀-ۿ])'), 'ملا'),
    # "Salama" -- glyph-width check against the PDF confirms every occurrence's
    # lam glyph is ~2x the width of a plain lam (e.g. in "علي"/"سليمان" on the
    # same pages), i.e. it's the lam-alef ligature glyph, not a real bare "سلمة".
    (re.compile(r'(?<![؀-ۿ])سلمة(?![؀-ۿ])'), 'سلامة'),
]
fused_pat = re.compile(r'(بو|أبو|ابو)(صلح|هلل|جلل|طلل)(?![؀-ۿ])')
_fused_map = {'صلح':'صلاح','هلل':'هلال','جلل':'جلال','طلل':'طلال'}
def fix_fused(s):
    return fused_pat.sub(lambda m: m.group(1) + _fused_map[m.group(2)], s)

def clean(s):
    if not s:
        return s
    s = normalize_allah(s)
    for v in abdullah_variants:
        s = s.replace(v, 'عبدالله')
    s = p_bin.sub('بن', s)
    s = p_slash_bn.sub(' بن', s)
    s = p_dr_fwd.sub('د. ', s)
    s = p_dr_rev.sub('د. ', s)
    s = p_sh_fwd.sub('الشيخ ', s)
    s = p_sh_rev.sub('الشيخ ', s)
    s = p_lead_sl.sub('', s)
    s = p_hasan.sub('حسن', s)
    for p, r in lam_alef_fixes:
        s = p.sub(r, s)
    s = fix_fused(s)
    s = s.replace('آالء', 'آلاء')
    s = re.sub(r'\s{2,}', ' ', s).strip()
    return s

changed = []
def tc(s, ctx):
    c = clean(s)
    if c != s:
        changed.append((ctx, s, c))
    return c

for pg, n in by_page.items():
    for field in ['name_chain', 'self_name', 'comments', 'freeform_text']:
        if n.get(field):
            n[field] = tc(n[field], f'page{pg}.{field}')
    for c in n.get('children', []):
        for field in ['name', 'kunya', 'husband', 'date']:
            if c.get(field):
                c[field] = tc(c[field], f'page{pg}.child.{field}')
    for w in n.get('wives', []):
        if w.get('name'):
            w['name'] = tc(w['name'], f'page{pg}.wife')

# known one-off correction: a substring-overlap earlier produced 'بعبدالله';
# guard against it happening again from the ordered abdullah_variants list.
for pg, n in by_page.items():
    for field in ['name_chain', 'self_name']:
        if n.get(field):
            n[field] = n[field].replace('بعبدالله', 'عبدالله')

print('total changes:', len(changed))
json.dump(d, open('consolidated.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
