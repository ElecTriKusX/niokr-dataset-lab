import csv, json, tempfile, unittest
from pathlib import Path
from gisnauka_parser import import_corpus, read_db, reg_key
from niokr import write_audit, sample, export
from sampling import load_mapping

class PipelineTests(unittest.TestCase):
    def test_database_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); folder=root/'НИОКРТ';folder.mkdir()
            _,_,names,labels=load_mapping(Path(__file__).with_name('grnti_groups.json'))
            mapping=load_mapping(Path(__file__).with_name('grnti_groups.json'))[1]
            for k,group in enumerate(names):
                code=next(c for c,g in mapping.items() if g==group)
                for i in range(3):
                    # use group-specific words and enough distinct tokens to avoid accidental SimHash merges
                    import random,string
                    rng=random.Random(k*100+i); words=' '.join(''.join(rng.choices(string.ascii_lowercase,k=12)) for _ in range(45))
                    card={'registration_number':f'P{k}-{i}','created_date':'2022-01-01','name':words[:70],'annotation':words+' Задачи проекта: 1. Изучить структуру клеток. 2. Разработать модель процесса.','rubrics':[{'code':code}]}
                    (folder/f'{k}-{i}.json').write_text(json.dumps(card,ensure_ascii=False),encoding='utf-8')
            c,a,d=(root/x for x in ('corpus.sqlite','audit.sqlite','datasets.sqlite'))
            self.assertEqual(import_corpus(root,c)['counts']['nioktr'],30)
            with self.assertRaises(FileExistsError): import_corpus(root,c)
            self.assertEqual(write_audit(c,a)['eligible_representatives'],30)
            # No file access after import.
            import shutil; shutil.rmtree(folder)
            cfg=dict(seed=20261124,general=10,enriched=10,allocation='equal',period_min=1)
            m=sample(c,a,d,cfg); self.assertEqual((m['G'],m['E'],m['exclusions']),(10,10,20))
            with read_db(d,'dataset') as (db,_): self.assertEqual(db.execute('SELECT COUNT(DISTINCT cluster_id) FROM samples').fetchone()[0],20)
            out=root/'inputs.jsonl';self.assertEqual(export(d,out)['exported'],20);self.assertEqual(len(out.read_text().splitlines()),20)
    def test_registration(self):
        self.assertEqual(reg_key('АААА-Б21- 121011990136-8'),'121011990136-8');self.assertEqual(reg_key('None'),'')
    def test_incomplete_import_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'НИОКРТ').mkdir();(root/'НИОКРТ'/'bad.json').write_text('{')
            target=root/'corpus.sqlite'
            with self.assertRaises(json.JSONDecodeError): import_corpus(root,target)
            self.assertFalse(target.exists())

if __name__=='__main__': unittest.main()
