import unittest
from sampling import regex_tasks, assign, draw

class Tests(unittest.TestCase):
    def test_numbered(self):
        text='Задачи проекта: 1. Изучить структуру клеток. 2. Разработать модель процесса. Ожидаемые результаты: 1. Статья. 2. Доклад.'
        s,items,_=regex_tasks(text); self.assertEqual(s,2); self.assertEqual(len(items),2)
        for x in items: self.assertEqual(text[x['start']:x['end']],x['text'])
        self.assertNotIn('результаты',items[-1]['text'])
    def test_bullets(self):
        self.assertEqual(regex_tasks('Задачи проекта: - изучить структуру клеток; - разработать модель процесса.')[0],2)
    def test_unrelated(self):
        self.assertNotEqual(regex_tasks('Задачи проекта: Изучить клетки.\nОжидаемые результаты: 1. Изучить свойства вещества. 2. Разработать программу.')[0],2)
    def test_draw_reproducible(self):
        rows=[dict(category='a',period_v2='p',reg_key=str(i)) for i in range(20)]
        first,plan=draw(rows,{'a':'A'},5,42,'G')
        second,_=draw(list(reversed(rows)),{'a':'A'},5,42,'G')
        self.assertEqual(first,second)
        self.assertEqual(plan[0]['weight'],4)
        self.assertEqual(len(first),5)
    def test_mapping(self):
        x=assign({'reg_key':'a','codes':'34.11.01|76.01.01','year':'2014'},{'34':'bio','76':'med'},42)
        self.assertEqual(x['period_v2'],'<=2020'); self.assertEqual(x['categories'],'bio|med'); self.assertIn(x['category'],['bio','med'])
if __name__=='__main__':unittest.main()
