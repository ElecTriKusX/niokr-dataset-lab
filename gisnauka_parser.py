"""Import the saved GIS Nauka JSON corpus into a single immutable SQLite snapshot."""
import hashlib, json, re, sqlite3, unicodedata
from pathlib import Path
from contextlib import contextmanager, closing
from tempfile import NamedTemporaryFile
from deduplication import flatten_text, grnti_codes, norm, sha

KINDS={'НИОКРТ':'nioktr','Научный отчет':'report','Научный отчёт':'report','РИД':'rid'}

def reg_key(value):
    if re.sub(r"\s+", " ", str(value or "")).strip().casefold() in {"", "нет", "нет данных", "не указано", "не задано", "не заполнено", "отсутствует", "н/д", "-", "—", "none", "null", "nan", "n/a", "unknown"}: return ""
    return re.sub(r'^(?:А{4}|A{4})-[А-ЯA-Z]\d{2}-','',re.sub(r'\s+','',unicodedata.normalize('NFKC',str(value or ''))).upper())

def jd(obj): return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def metadata(db): return {k:json.loads(v) for k,v in db.execute('SELECT key,value FROM metadata')}
def setmeta(db, values): db.executemany('INSERT INTO metadata VALUES (?,?)',[(k,jd(v)) for k,v in values.items()])

@contextmanager
def new_db(path):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): raise FileExistsError(f'{path}: use a new database path; existing data not overwritten')
    with NamedTemporaryFile(dir=path.parent,suffix='.tmp',delete=False) as f: tmp=Path(f.name)
    try:
        with closing(sqlite3.connect(tmp)) as db:
            db.execute('PRAGMA foreign_keys=ON'); db.execute('PRAGMA journal_mode=DELETE')
            db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            yield db
            db.commit()
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ValueError('SQLite quick_check failed')
        if path.exists(): raise FileExistsError(path)
        tmp.rename(path)
    finally: tmp.unlink(missing_ok=True)

@contextmanager
def read_db(path,kind):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        m=metadata(db)
        if m.get('kind')!=kind or m.get('version')!=1 or not m.get('complete'): raise ValueError(f'Invalid {kind} database: {path}')
        yield db,m

def source_files(root):
    root=Path(root)
    if not root.is_dir(): raise FileNotFoundError(root)
    for p in sorted(root.rglob('*.json'),key=lambda x:x.relative_to(root).as_posix()):
        rel=p.relative_to(root); kind=next((KINDS[x] for x in [root.name,*rel.parts[:-1]] if x in KINDS),None)
        if kind is None:
            kind=next((k for prefix,k in [('ниокр_','nioktr'),('отчет_','report'),('рид_','rid')] if p.name.lower().startswith(prefix)),None)
        if kind: yield rel.as_posix(),p,kind

def import_corpus(root,output):
    return build_corpus(((key,kind,p.read_bytes()) for key,p,kind in source_files(root)),output,"local_files")

def build_corpus(items,output,origin):
    counts={}; h=hashlib.sha256()
    with new_db(output) as db:
        db.executescript('''CREATE TABLE cards(source_key TEXT PRIMARY KEY,kind TEXT NOT NULL,registration TEXT NOT NULL,year INTEGER NOT NULL,created_date TEXT NOT NULL,title TEXT NOT NULL,annotation TEXT NOT NULL,codes TEXT NOT NULL,project_link TEXT NOT NULL,norm_hash TEXT NOT NULL,raw_hash TEXT NOT NULL,raw_json BLOB NOT NULL);
        CREATE INDEX cards_reg ON cards(kind,registration,year); CREATE INDEX cards_link ON cards(project_link);''')
        for i,(key,kind,raw) in enumerate(items,1):
            obj=json.loads(raw.decode('utf-8-sig'))
            if not isinstance(obj,dict): raise ValueError(f'Non-object JSON: {key}')
            title=flatten_text(obj.get('name')); ann=flatten_text(obj.get('annotation' if kind=='nioktr' else 'abstract'))
            date=str(obj.get('created_date') or ''); year=int(date[:4]) if date[:4].isdigit() else 0
            raw_hash=hashlib.sha256(raw).hexdigest(); codes='|'.join(grnti_codes(obj.get('rubrics')))
            db.execute('INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(key,kind,reg_key(obj.get('registration_number')),year,date,title,ann,codes,reg_key(obj.get('nioktr')) if kind!='nioktr' and isinstance(obj.get('nioktr'),str) else '',sha(norm((title+'\n'+ann).strip())),raw_hash,raw))
            h.update((jd([key,raw_hash])+'\n').encode()); counts[kind]=counts.get(kind,0)+1
            if i%10000==0: db.commit(); print(f'Imported {i}',flush=True)
        if not counts.get('nioktr'): raise ValueError('No NIOKTR cards found')
        m=dict(kind='corpus',version=1,complete=True,snapshot=h.hexdigest(),counts=counts,origin=origin)
        setmeta(db,m)
    return m

def api_items(from_year,to_year):
    """Download monthly responses; fail the snapshot if any request fails."""
    from urllib.request import urlopen, Request
    from urllib.parse import urlencode
    from datetime import date
    from time import sleep
    now=date.today()
    if not 1900<=from_year<=to_year<=now.year: raise ValueError('Invalid years')
    for year in range(from_year,to_year+1):
        for month in range(1,(now.month if year==now.year else 12)+1):
            for kind,api_type in [('nioktr','nioktr'),('report','ikrbs'),('rid','rid')]:
                url='https://gisnauka.ru/api/egisu/open-data?'+urlencode(dict(year=year,month=f'{month:02d}',card_type=api_type))
                for attempt in range(3):
                    try:
                        with urlopen(Request(url,headers={'Accept':'application/json','User-Agent':'NIOKR dataset laboratory'}),timeout=180) as res: obj=json.load(res)
                        cards=obj['cards']
                        if not isinstance(cards,list): raise ValueError('cards is not a list')
                        break
                    except Exception:
                        if attempt==2: raise
                        sleep(2**attempt)
                print(f'{year}-{month:02d} {kind}: {len(cards)}',flush=True)
                for i,card in enumerate(cards):
                    yield f'api/{kind}/{year}-{month:02d}/{i:07d}',kind,jd(card).encode('utf-8')
                sleep(.2)

def download_corpus(from_year,to_year,output):
    return build_corpus(api_items(from_year,to_year),output,'api_monthly')

if __name__=='__main__':
    import argparse
    from datetime import date
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('root',type=Path,nargs='?');p.add_argument('--out',type=Path,default=Path('data/corpus.sqlite'));p.add_argument('--from-year',type=int);p.add_argument('--to-year',type=int,default=date.today().year);a=p.parse_args()
    if (a.root is None)==(a.from_year is None): p.error('Specify root OR --from-year')
    print(jd(import_corpus(a.root,a.out) if a.root else download_corpus(a.from_year,a.to_year,a.out)))
