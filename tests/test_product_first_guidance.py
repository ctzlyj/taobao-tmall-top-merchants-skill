from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class ProductFirstGuidanceTests(unittest.TestCase):
 def test_region_recruitment_requires_product_first_evidence(self):
  guide=(ROOT/'references/product-first-recruitment.md').read_text(encoding='utf-8')
  for marker in ['具体商品','图片','saleNumber','库存','累计销量','市场摊位','接听人','主体','未预约','头部','品牌方','生产方','店铺执照方']:
   self.assertIn(marker,guide)
  self.assertIn('references/product-first-recruitment.md',(ROOT/'SKILL.md').read_text(encoding='utf-8'))
 def test_phone_hunt_uses_number_attribution_as_corroboration(self):
  guide=(ROOT/'references/public-phone-hunt.md').read_text(encoding='utf-8')
  for marker in ['号码归属地旁证','一致只作旁证','代记账']:
   self.assertIn(marker,guide)
if __name__=='__main__':unittest.main()
