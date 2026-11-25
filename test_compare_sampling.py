import unittest
from compare_sampling import design_quota, absence, assess, Cell

class CompareTests(unittest.TestCase):
    def test_equal_and_proportional(self):
        cap={('a','p'):90,('b','p'):10}
        self.assertEqual(design_quota(cap,20,'equal'),{('a','p'):10,('b','p'):10})
        self.assertEqual(design_quota(cap,20,'proportional'),{('a','p'):18,('b','p'):2})
        self.assertEqual(sum(design_quota(cap,20,'hybrid').values()),20)
    def test_minimum_infeasible(self):
        with self.assertRaises(ValueError): design_quota({('a','p'):100,('b','p'):100},3,'equal',2)
    def test_census_small(self):
        self.assertEqual(design_quota({('a','p'):1,('b','p'):100},10,'equal',2),{('a','p'):1,('b','p'):9})
    def test_absence(self):
        self.assertAlmostEqual(absence(10,3,2),21/45)
        self.assertEqual(absence(10,3,8),0)
    def test_variance_and_coverage(self):
        c=Cell()
        for x in (0,0,1,1): c.add(['01'] if x else ['02'],'a',(x,x,0,0,1))
        res,proxy,refs,codes=assess({('a','p'):c},{('a','p'):2},['a'],['01','02'])
        self.assertAlmostEqual(proxy[0]['design_se']**2,1/12)
        self.assertAlmostEqual(proxy[0]['variance_ratio_vs_srs'],1)
        self.assertAlmostEqual(res['expected_raw_codes'],5/3)
        self.assertAlmostEqual(res['kish_n'],2)
        self.assertAlmostEqual(res['binary_worst_case_se']**2,1/12)
        res,proxy,*_=assess({('a','p'):c},{('a','p'):4},['a'],['01','02'])
        self.assertEqual(proxy[0]['design_se'],0)
    def test_missing_reference_domain(self):
        c=Cell(); c.add(['01'],'a',(1,1,0,0,1)); c.add(['01'],'a',(1,1,0,0,1))
        res,*_=assess({('a','p'):c},{('a','p'):2},['a','b'],['01'])
        self.assertEqual(res['min_ref10_expected'],0)
        self.assertEqual(res['max_ref10_absence'],1)
