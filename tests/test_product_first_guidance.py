from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class ProductFirstGuidanceTests(unittest.TestCase):
 def test_region_recruitment_requires_product_first_evidence(self):
  guide=(ROOT/'references/product-first-recruitment.md').read_text(encoding='utf-8')
  for marker in ['具体商品','图片','saleNumber','库存','累计销量','市场摊位','接听人','主体','未预约','头部']:
   self.assertIn(marker,guide)
  self.assertIn('references/product-first-recruitment.md',(ROOT/'SKILL.md').read_text(encoding='utf-8'))
if __name__=='__main__':unittest.main()
