#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fixed GRNTI mapping, bounded Regex V2 and stratified sampling."""
import hashlib
import json
import random
import re
from collections import defaultdict
from pathlib import Path
from compare_sampling import design_quota

HERE = Path(__file__).resolve().parent
PERIODS = ('<=2020', '2021-2023', '2024+')
CODE = re.compile(r'\d{2}(?:\.\d{2}){0,2}\Z')
WORDS = re.compile(r'[^\W\d_]+', re.UNICODE)
# Heading and items must be in the SAME bounded block. Conservative, not gold.
HEADING = re.compile(r'(?im)(?<![\w])(?:основные\s+|следующие\s+|конкретные\s+)?задачи(?:\s+(?:научного\s+исследования|исследования|проекта|работы|нир|ниокр(?:тр)?))?\s*[:：\-–—]|(?im:для\s+достижения\s+[^\n:]{1,180}\s+(?:решить|решаются|поставлены)\s+(?:следующие\s+)?задачи\s*:)')
STOP = re.compile(r'(?im)(?:^|(?<=[.!?;])\s+)(?:ожидаемые\s+результаты|результаты\s+(?:исследования|работы|проекта)|научная\s+новизна|практическая\s+значимость|актуальность|методы\s+исследования|цель\s+(?:работы|исследования|проекта)|заключение|этапы\s+(?:работы|исследования))\s*[:：\-–—]')
MARK = re.compile(r'(?im)(?:^|(?<=[\s;:]))(?:(?:задача\s+)?(?P<num>\d{1,2})[.)](?!\d)\s*|(?P<bullet>[•●▪–—-])\s*(?=[А-Яа-яЁёA-Za-z]))')
ACTION = re.compile(r'(?i)\b(?:анализ\w*|исслед\w*|изуч\w*|разработ\w*|созда\w*|создан\w*|определ\w*|выяв\w*|оцен\w*|провер\w*|прове\w*|получ\w*|синтез\w*|моделир\w*|постро\w*|формир\w*|обосн\w*|установ\w*|развити\w*|сравн\w*|измер\w*|подготов\w*|изготов\w*|испыт\w*|разработа\w*|обработ\w*|эксперимент\w*|сбор\w*|собра\w*|расчет\w*|расчёт\w*|характериз\w*|опис\w*|уточн\w*)\b')


def digest(s): return hashlib.sha256(s.encode('utf-8')).hexdigest()
def file_digest(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()
def canonical(s): return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', str(s).casefold())).strip()
def txt(v):
    if isinstance(v,str): return v
    if isinstance(v,list): return ' '.join(txt(x) for x in v)
    if isinstance(v,dict):
        for k in ('value','text','name','description'):
            if k in v: return txt(v[k])
    return ''
def load_mapping(path):
    cfg=json.loads(path.read_text(encoding='utf-8-sig')); mapping={}; names={}; labels={}
    for g in cfg['groups']:
        gid=g['id']
        if gid in names: raise ValueError('duplicate group '+gid)
        names[gid]=g['name']
        for c,label in g['rubrics'].items():
            if c in mapping or not re.fullmatch(r'\d{2}',c): raise ValueError('invalid/duplicate code '+c)
            mapping[c]=gid; labels[c]=label
    return cfg,mapping,names,labels

def parse_codes(codes):
    items=sorted(set(c.strip() for c in codes if c.strip()))
    return [c for c in items if CODE.fullmatch(c)], [c for c in items if not CODE.fullmatch(c)]

def assign(r, mapping, seed):
    codes,bad=parse_codes(r['codes'].split('|'))
    majors=sorted({c.split('.')[0] for c in codes})
    groups=sorted({mapping[c] for c in majors if c in mapping})
    unknown=sorted(set(majors)-set(mapping))
    # Stable hash among distinct GROUPS, not among codes: no low-code preference.
    group=groups[int(digest(f'{seed}|{r["reg_key"]}|grnti')[:16],16)%len(groups)] if groups else 'unresolved'
    year=int(r['year'] or 0)
    period='<=2020' if 1900<=year<=2020 else '2021-2023' if 2021<=year<=2023 else '2024+' if 2024<=year<=2100 else 'unknown_year'
    return dict(r, category=group, categories='|'.join(groups), major_codes='|'.join(majors),
                unmapped_codes='|'.join(unknown+bad), category_count=len(groups), period_v2=period)

def regex_tasks(text):
    """Return signal, literal candidate spans in original annotation, reason. No inference."""
    best=(0, [], 'no_task_heading')
    for h in HEADING.finditer(text):
        end=min(len(text),h.end()+12000)
        stop=STOP.search(text,h.end(),end)
        if stop: end=stop.start()
        next_heading=HEADING.search(text,h.end(),end)
        if next_heading: end=next_heading.start()
        block=text[h.end():end]
        marks=list(MARK.finditer(block)); items=[]; nums=[]
        for i,m in enumerate(marks):
            a=h.end()+m.end(); b=h.end()+(marks[i+1].start() if i+1<len(marks) else len(block))
            while a<b and text[a].isspace(): a+=1
            while b>a and text[b-1].isspace(): b-=1
            t=text[a:b]
            if len(WORDS.findall(t))<3 or len(t)>2000 or not ACTION.search(t): continue
            items.append({'start':a,'end':b,'text':t}); nums.append(m.group('num'))
        # Contiguous numbering (starting at 1) or bullets; reject unrelated numeric patterns.
        seq=all(n is not None for n in nums) and [int(n) for n in nums]==list(range(1,len(nums)+1))
        bullets=bool(nums) and all(n is None for n in nums)
        if len(items)>=2 and (seq or bullets): return 2,items,'bounded_numbered_or_bullet_list'
        # Semicolon-separated action clauses: review candidates, never automatic strict.
        if best[0]<1: best=(1,items,'heading_without_verified_list')
    if best[0]==0 and len(list(MARK.finditer(text)))>=2: return 1,[],'list_without_heading'
    return best

def draw(rows, names, total, seed, pool, allocation="equal", period_min=2):
    cells=defaultdict(list)
    for r in rows: cells[r['category'],r['period_v2']].append(r)
    if set(g for g,p in cells)!=set(names): raise ValueError('some configured groups have no records in this pool')
    quota=design_quota({k:len(v) for k,v in cells.items()},total,allocation,period_min)
    rng=random.Random(seed); chosen=[]; plan=[]
    for (g,p),n in sorted(quota.items()):
        N=len(cells[g,p]); plan.append(dict(pool=pool,category=g,period=p,N=N,n=n,probability=n/N,weight=N/n))
        for r in rng.sample(sorted(cells[g,p],key=lambda r:r['reg_key']),n):
            chosen.append(dict(r,pool=pool,N_h=N,n_h=n,inclusion_probability=n/N,design_weight=N/n))
    return chosen,plan

