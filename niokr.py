"""Database-first NIOKR lab. Python >=3.10, standard library only."""
import argparse, csv, hashlib, json, random, zipfile, io
from pathlib import Path, PureWindowsPath
from collections import Counter
from gisnauka_parser import import_corpus, read_db, new_db, setmeta, jd
from deduplication import cluster, explicit_signal, simhash64, word_count, year_period
from sampling import assign, draw, regex_tasks, load_mapping, digest, file_digest
from compare_sampling import compare

HERE=Path(__file__).resolve().parent
INDEX_FIELDS='reg_key path year period primary_major group codes eligible words chars explicit_signal norm_hash cluster_id simhash'.split()

def csv_rows(path):
    with open(path,encoding='utf-8-sig',newline='') as f: yield from csv.DictReader(f)

def write_audit(corpus, output, legacy_index=None, legacy_manifest=None, previous_review=None):
    """Store a new audit or restore exact historical index order and clusters."""
    with read_db(corpus,'corpus') as (src,cm), new_db(output) as db:
        db.executescript('''CREATE TABLE population(ordinal INTEGER PRIMARY KEY,reg_key TEXT UNIQUE NOT NULL,source_key TEXT NOT NULL,cluster_id TEXT NOT NULL,eligible INTEGER NOT NULL,row_json TEXT NOT NULL,signal_v2 INTEGER,regex_reason TEXT);
        CREATE INDEX population_cluster ON population(cluster_id); CREATE TABLE exclusions(cluster_id TEXT PRIMARY KEY,reason TEXT NOT NULL);''')
        # Preserve old row order: representative is first in each historical cluster.
        old=set(); stats={}; top=[]
        if legacy_index:
            if not legacy_manifest or not previous_review: raise ValueError('restore requires index, manifest and previous review')
            lm=json.loads(Path(legacy_manifest).read_text(encoding='utf-8-sig'))
            if lm.get('partial_run'): raise ValueError('partial historical index')
            basename={}
            for r in src.execute("SELECT source_key,registration,norm_hash,codes FROM cards WHERE kind='nioktr'"):
                name=Path(r['source_key']).name
                if name in basename: raise ValueError(f'Ambiguous NIOKTR filename: {name}')
                basename[name]=tuple(r)
            records=[]
            for i,r in enumerate(csv_rows(legacy_index),1):
                key=PureWindowsPath(r['path']).name
                if key not in basename: raise ValueError(f'Missing historical card: {key}')
                source,reg,nh,codes=basename[key]
                if nh!=r['norm_hash'] or (reg and reg!=r['reg_key']) or codes!=r['codes']:
                    raise ValueError(f'Historical record differs: {r["reg_key"]}')
                r['source_key']=source; records.append(r)
                if i%10000==0: print(f'Validated {i}',flush=True)
            oldrows=list(csv_rows(previous_review)); old={r['cluster_id'] for r in oldrows}
            pairs={(r['reg_key'],r['cluster_id']) for r in records}
            if not oldrows or not all((r['reg_key'],r['cluster_id']) in pairs for r in oldrows): raise ValueError('Historical review index does not match corpus')
            reps={}
            for r in records: reps.setdefault(r['cluster_id'],r)
            checks={'unique_registration_records_read':len(records),'detected_duplicate_clusters':len(reps),'eligible_cluster_representatives':sum(r['eligible']=='1' for r in reps.values()),'eligible_explicit_regex_candidates':sum(r['eligible']=='1' and r['explicit_signal']=='2' for r in reps.values())}
            if any(lm[k]!=v for k,v in checks.items()): raise ValueError(f'Historical count mismatch: {checks}')
            stats=checks; top=lm['selected_major_groups']; legacy_sha=file_digest(legacy_index)
        else:
            if legacy_manifest or previous_review: raise ValueError('historical inputs require --index')
            # Deterministic selection among versions; fresh snapshot need not match historical audit row order.
            records=[]; seen=set()
            for r in src.execute("SELECT source_key,registration,year,created_date,title,annotation,codes,norm_hash FROM cards WHERE kind='nioktr' ORDER BY registration,year DESC,created_date DESC,source_key DESC"):
                key=r['registration'] or 'PATH:'+r['source_key']
                if key in seen: continue
                seen.add(key); text=(r['title']+'\n'+r['annotation']).strip(); words=word_count(text); codes=r['codes']; year=r['year']; major=min((c.split('.')[0] for c in codes.split('|') if c),default='UNCLASSIFIED')
                records.append(dict(reg_key=key,path=r['source_key'],source_key=r['source_key'],year=str(year),period=year_period(year),primary_major=major,group=major,codes=codes,eligible=str(int(bool(r['annotation'].strip()) and len(text)>=250 and words>=30)),words=str(words),chars=len(text),explicit_signal=str(explicit_signal(text)),norm_hash=r['norm_hash'],simhash=simhash64(r['title'],r['annotation'])))
                if len(records)%10000==0: print(f'Indexed {len(records)}',flush=True)
            if not records: raise ValueError('Empty corpus')
            reps,stats=cluster(records); top=[k for k,v in Counter(r['primary_major'] for r in reps if r['eligible']=='1' and r['primary_major']!='UNCLASSIFIED').most_common(12)]
            for r in records: r['simhash']=f'{r["simhash"]:016x}'; r['chars']=str(r['chars']); r['group']=r['primary_major'] if r['primary_major'] in top else 'OTHER'
            legacy_sha=None
        db.executemany('INSERT INTO exclusions VALUES (?,?)',[(x,'historical diagnostic') for x in sorted(old)])
        # Read raw annotation from SQLite, never from individual files. Cache Regex V2 across runs.
        lookup=src.cursor(); batch=[]; reps_seen=set(); strict=0; eligible=0
        for i,r in enumerate(records,1):
            first=r['cluster_id'] not in reps_seen; reps_seen.add(r['cluster_id']); signal=None; reason=None
            if first and r['eligible']=='1':
                ann=lookup.execute('SELECT annotation FROM cards WHERE source_key=?',(r['source_key'],)).fetchone()[0]
                signal,_,reason=regex_tasks(ann); strict+=signal==2; eligible+=1
            batch.append((i,r['reg_key'],r['source_key'],r['cluster_id'],int(r['eligible']),jd({k:r[k] for k in INDEX_FIELDS}),signal,reason))
            if len(batch)>=1000: db.executemany('INSERT INTO population VALUES (?,?,?,?,?,?,?,?)',batch);batch.clear()
        if batch: db.executemany('INSERT INTO population VALUES (?,?,?,?,?,?,?,?)',batch)
        m=dict(kind='audit',version=1,complete=True,corpus_snapshot=cm['snapshot'],legacy_index_sha256=legacy_sha,records=len(records),clusters=len(reps_seen),eligible_representatives=eligible,strict_v2_all_eligible=strict,old_exclusions=len(old),v1_top=top,diagnostics=stats,regex_version='bounded-v2',dedup_version='simhash-v1',historical=bool(legacy_index))
        # Fingerprint all logical audit rows, rather than DB bytes.
        h=hashlib.sha256()
        for row in db.execute('SELECT * FROM population ORDER BY ordinal'): h.update((jd(list(row))+'\n').encode())
        h.update(jd(sorted(old)).encode());m['audit_snapshot']=h.hexdigest();setmeta(db,m)
    return m

def get_frame(db,mapping,seed):
    old={r[0] for r in db.execute('SELECT cluster_id FROM exclusions')}; seen=set(); pool=[]
    for r in db.execute('SELECT * FROM population ORDER BY ordinal'):
        if r['cluster_id'] in seen: continue
        seen.add(r['cluster_id'])
        if not r['eligible'] or r['cluster_id'] in old: continue
        x=assign(json.loads(r['row_json']),mapping,seed)
        if x['category']=='unresolved' or x['period_v2']=='unknown_year' or x['reg_key'].startswith('PATH:'): continue
        pool.append(dict(x,source_key=r['source_key'],signal_v2=r['signal_v2'],regex_reason=r['regex_reason']))
    return pool,old

def sample(corpus,audit,output,config,archive=None):
    seed=config['seed']; mapping_path=HERE/'grnti_groups.json'; _,mapping,names,_=load_mapping(mapping_path)
    with read_db(corpus,'corpus') as (src,cm),read_db(audit,'audit') as (adb,am),new_db(output) as db:
        if am['corpus_snapshot']!=cm['snapshot']: raise ValueError('Audit belongs to another corpus')
        pool,old=get_frame(adb,mapping,seed)
        general,plan=draw(pool,names,config['general'],seed,'G',config['allocation'],config['period_min']); clusters={r['cluster_id'] for r in general}
        strict=[r for r in pool if r['signal_v2']==2 and r['cluster_id'] not in clusters]
        enriched,plan_e=draw(strict,names,config['enriched'],seed+1,'E',config['allocation'],config['period_min'])
        chosen=[]
        for part,tag in [(general,'G'),(enriched,'E')]:
            for i,r in enumerate(part,1): chosen.append(dict(r,sample_id=f'{tag}{i:04d}'))
        order=random.Random(seed+2); shuffled=[r['sample_id'] for r in chosen];order.shuffle(shuffled)
        aliases=[f'P{i:04d}' for i in range(1,len(chosen)+1)];order.shuffle(aliases); keys=dict(zip(shuffled,aliases))
        if len({r['cluster_id'] for r in chosen})!=len(chosen): raise ValueError('Duplicate selected cluster')
        db.executescript('''CREATE TABLE samples(blind_id TEXT PRIMARY KEY,sample_id TEXT UNIQUE,registration TEXT NOT NULL,source_key TEXT NOT NULL,cluster_id TEXT UNIQUE NOT NULL,pool TEXT NOT NULL,weight REAL NOT NULL,source_hash TEXT NOT NULL,selection_json TEXT NOT NULL,input_json TEXT NOT NULL,regex_json TEXT NOT NULL);
        CREATE TABLE quotas(pool TEXT,category TEXT,period TEXT,population_size INTEGER,sample_size INTEGER,probability REAL,weight REAL,PRIMARY KEY(pool,category,period));
        CREATE TABLE test_exclusions(cluster_id TEXT PRIMARY KEY);''')
        db.executemany('INSERT INTO quotas VALUES (?,?,?,?,?,?,?)',[(p['pool'],p['category'],p['period'],p['N'],p['n'],p['probability'],p['weight']) for p in plan+plan_e])
        for r in chosen:
            card=src.execute('SELECT title,annotation,norm_hash FROM cards WHERE source_key=?',(r['source_key'],)).fetchone(); sid=r['sample_id']; blind=keys[sid]
            if card['norm_hash']!=r['norm_hash']: raise ValueError('Source hash mismatch')
            s,items,reason=regex_tasks(card['annotation'])
            if s!=r['signal_v2']: raise ValueError('Regex cache mismatch')
            inp=dict(sample_id=blind,name=card['title'],annotation=card['annotation']); labels=dict(sample_id=blind,signal=s,reason=reason,candidate_spans=items)
            db.execute('INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?)',(blind,sid,r['reg_key'],r['source_key'],r['cluster_id'],r['pool'],r['design_weight'],card['norm_hash'],jd(r),jd(inp),jd(labels)))
        excl=old|{r['cluster_id'] for r in chosen}; db.executemany('INSERT INTO test_exclusions VALUES (?)',[(x,) for x in sorted(excl)])
        m=dict(kind='dataset',version=1,complete=True,corpus_snapshot=cm['snapshot'],audit_snapshot=am['audit_snapshot'],mapping_sha256=file_digest(mapping_path),config=config,pilot_frame=len(pool),strict_total=sum(r['signal_v2']==2 for r in pool),G=len(general),E=len(enriched),exclusions=len(excl),historical_verified=False)
        if archive:
            verify_archive(db,archive,am,m); m['historical_verified']=True
        setmeta(db,m)
    return m

def verify_archive(db,archive,am,m):
    with zipfile.ZipFile(archive) as z:
        man=json.loads(z.read('manifest.json'))
        if man['source_index_sha256']!=am['legacy_index_sha256'] or man['mapping_sha256']!=m['mapping_sha256']: raise ValueError('Saved pilot belongs to different index or mapping')
        for k in ('seed','allocation','period_min'):
            if man[k]!=m['config'][k]: raise ValueError('Historical pilot config differs: '+k)
        for name in ('11_pilot_index.csv','12_review_key.csv','14_llm_inputs.jsonl','15_regex_candidates.jsonl','16_exclude_from_future_test.csv'):
            if hashlib.sha256(z.read(name)).hexdigest()!=man['output_sha256'][name]: raise ValueError('Saved archive file hash mismatch: '+name)
        def rows(name): return list(csv.DictReader(io.StringIO(z.read(name).decode('utf-8-sig'))))
        expected={r['sample_id']:(r['reg_key'],r['cluster_id'],r['pool']) for r in rows('11_pilot_index.csv')}
        actual={r[0]:tuple(r[1:]) for r in db.execute('SELECT sample_id,registration,cluster_id,pool FROM samples')}
        if expected!=actual: raise ValueError('Historical pilot membership differs')
        if {r['sample_id']:r['blind_id'] for r in rows('12_review_key.csv')}!=dict(db.execute('SELECT sample_id,blind_id FROM samples')): raise ValueError('Historical alias mismatch')
        for file,col in [('14_llm_inputs.jsonl','input_json'),('15_regex_candidates.jsonl','regex_json')]:
            target={r['sample_id']:r for r in map(json.loads,z.read(file).decode().splitlines())}
            actual={r[0]:json.loads(r[1]) for r in db.execute(f'SELECT blind_id,{col} FROM samples')}
            if target!=actual: raise ValueError('Historical payload mismatch: '+file)
        if {r['cluster_id'] for r in rows('16_exclude_from_future_test.csv')}!={r[0] for r in db.execute('SELECT cluster_id FROM test_exclusions')}: raise ValueError('Historical exclusions differ')

def comparison(audit,output,config):
    _,mp,names,labels=load_mapping(HERE/'grnti_groups.json')
    with read_db(audit,'audit') as (src,am),new_db(output) as db:
        pool,_=get_frame(src,mp,config['seed'])
        db.execute('CREATE TABLE results(name TEXT,ordinal INTEGER,payload TEXT NOT NULL,PRIMARY KEY(name,ordinal))')
        def save_csv(path,rows,fields=None): db.executemany('INSERT INTO results VALUES (?,?,?)',[(path.stem,i,jd(r)) for i,r in enumerate(rows)])
        def save_json(path,obj): db.execute('INSERT INTO results VALUES (?,?,?)',(path.stem,0,jd(obj)))
        rows,fail=compare(pool,mp,names,labels,am['v1_top'],config['seed'],Path('.'),config['compare_budgets'],config['period_min'],save_csv,save_json,digest)
        m=dict(kind='comparison',version=1,complete=True,audit_snapshot=am['audit_snapshot'],config=config,feasible=len(rows),infeasible=len(fail),note='E uses V1 proxy; not actual decomposition quality');setmeta(db,m)
    return m

def export(dataset,output):
    with read_db(dataset,'dataset') as (db,m):
        with open(output,'x',encoding='utf-8') as f:
            for r in db.execute('SELECT input_json FROM samples ORDER BY blind_id'): f.write(r[0]+'\n')
    return {'exported':m['G']+m['E'],'path':str(output)}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,default=HERE/'config.json');sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('import');q.add_argument('--root',type=Path,required=True)
    q=sub.add_parser('audit');q.add_argument('--index',type=Path);q.add_argument('--manifest',type=Path);q.add_argument('--previous-review',type=Path)
    q=sub.add_parser('pilot');q.add_argument('--verify',type=Path)
    sub.add_parser('compare');q=sub.add_parser('export');q.add_argument('output',type=Path)
    sub.add_parser('status');a=p.parse_args();c=json.loads(a.config.read_text(encoding='utf-8'));base=a.config.resolve().parent;d=base/c['data_dir']; corpus=d/'corpus.sqlite'; audit=d/'audit.sqlite'; datasets=d/'datasets.sqlite'; comparison_db=d/'comparison.sqlite'
    settings=c['sampling']
    if a.command=='import': result=import_corpus(a.root,corpus)
    elif a.command=='audit': result=write_audit(corpus,audit,a.index,a.manifest,a.previous_review)
    elif a.command=='pilot': result=sample(corpus,audit,datasets,settings,a.verify)
    elif a.command=='compare': result=comparison(audit,comparison_db,settings)
    elif a.command=='export': result=export(datasets,a.output)
    else:
        result={}
        for path,kind in [(corpus,'corpus'),(audit,'audit'),(datasets,'dataset'),(comparison_db,'comparison')]:
            if path.exists():
                with read_db(path,kind) as (_,m): result[path.name]=m
            else: result[path.name]='not created'
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
