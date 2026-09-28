import argparse
import json
import re
import math
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from openpyxl import load_workbook

from common import load_job
from subject_identity import valid_qualification
from workbook_formulas import expected_formulas
from build_workbook import selected_company, candidate_identity, extract_contacts


SECRET_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._-]{12,}|Authorization\s*[=:]", re.I)


def normalize_identity(value):
    return re.sub(r"[\s（）()·•,，。]", "", str(value or "")).lower()


def verify(job_dir, workbook_path=None):
    job_dir = Path(job_dir).resolve()
    job = load_job(job_dir)
    audit = json.loads((job_dir / "assortment_audit.json").read_text(encoding="utf-8"))
    formal_records = [row for row in audit.values() if row.get("passes_minimum")]
    for row in formal_records:
        assert row["target_spu"] >= job["min_spu"], row["shop_name"]
        assert row["target_share"] >= job["min_share"], row["shop_name"]
    workbook_path = Path(workbook_path) if workbook_path else job_dir / "outputs" / f'{job["category"]}_淘宝天猫TOP商家招商表.xlsx'
    with ZipFile(workbook_path) as archive:
        bad = archive.testzip()
        assert bad is None, bad
    formula_book = load_workbook(workbook_path, data_only=False)
    value_book = load_workbook(workbook_path, data_only=True)
    required = ["概览", "正式招商商家", "主体核验", "未确认字段", "淘汰商家", "口径与复用"]
    assert formula_book.sheetnames == required, formula_book.sheetnames
    formal_sheet = value_book["正式招商商家"]
    headers = {cell.value: cell.column for cell in formal_sheet[3]}
    needed = {"店铺名", "类目", "目标SPU", "精确店铺SPU", "相关占比", "匹配等级", "公司名称", "统一社会信用代码", "主体一致性"}
    assert needed.issubset(headers), "正式招商商家缺少必要字段"
    actual_rows = [row for row in range(4, formal_sheet.max_row + 1)
                   if any(formal_sheet.cell(row, column).value is not None for column in headers.values())]
    actual_keys = Counter((formal_sheet.cell(row, headers["类目"]).value,
                           formal_sheet.cell(row, headers["店铺名"]).value) for row in actual_rows)
    expected_keys = Counter((record.get("category") or job["category"], record["shop_name"]) for record in formal_records)
    assert actual_keys == expected_keys, "正式商家集合与审计证据不一致：漏行、多行或串店"
    assert all(count == 1 for count in actual_keys.values()), "正式商家重复"
    expected_records = {(record.get("category") or job["category"], record["shop_name"]): record for record in formal_records}
    for row in actual_rows:
        key = (formal_sheet.cell(row, headers["类目"]).value, formal_sheet.cell(row, headers["店铺名"]).value)
        record = expected_records[key]
        target = formal_sheet.cell(row, headers["目标SPU"]).value
        total = formal_sheet.cell(row, headers["精确店铺SPU"]).value
        assert target == record["target_spu"] and total == record["exact_shop_spu_seen"], f"{key}: SPU与来源不一致"
        assert isinstance(total, (int, float)) and total > 0 and 0 <= target <= total, f"{key}: SPU范围无效"
        share = formal_sheet.cell(row, headers["相关占比"]).value
        assert isinstance(share, (int, float)) and math.isclose(share, target / total, abs_tol=1e-10), f"{key}: 公式未计算或结果不正确"
        grade = "高匹配" if target >= job["min_spu"] and share >= job["high_match_share"] else "达标" if target >= job["min_spu"] and share >= job["min_share"] else "不达标"
        assert formal_sheet.cell(row, headers["匹配等级"]).value == grade, f"{key}: 匹配等级公式未计算或结果错误"
        formula_sheet = formula_book["正式招商商家"]
        assert (formula_sheet.cell(row, headers["相关占比"]).value, formula_sheet.cell(row, headers["匹配等级"]).value) == expected_formulas(row, job), f"{key}: 关键公式被替换"
    errors = []
    for sheet in value_book.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    assert not SECRET_PATTERN.search(cell.value), f"possible secret in {sheet.title}!{cell.coordinate}"
                if isinstance(cell.value, str) and cell.value in {"#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A"}:
                    errors.append((sheet.title, cell.coordinate, cell.value))
    assert not errors, errors
    for sheet in formula_book.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    assert value_book[sheet.title][cell.coordinate].value is not None, f"uncomputed formula: {sheet.title}!{cell.coordinate}"
    qualifications_path = job_dir / "platform_qualifications.json"
    if qualifications_path.exists():
        qualifications = json.loads(qualifications_path.read_text(encoding="utf-8"))
        formal_sheet = value_book["正式招商商家"]
        headers = {cell.value: cell.column for cell in formal_sheet[3]}
        required_subject_columns = {"店铺名", "公司名称", "统一社会信用代码", "主体一致性"}
        assert required_subject_columns.issubset(headers), "正式招商商家缺少平台主体核验列"
        rows = {formal_sheet.cell(row, headers["店铺名"]).value: row for row in range(4, formal_sheet.max_row + 1)}
        for shop, qualification in qualifications.items():
            if shop not in {record["shop_name"] for record in formal_records}:
                continue
            if qualification.get("status") != "verified":
                continue
            assert valid_qualification(qualification), f"{shop}: 平台资质缺少完整主体或来源"
            assert shop in rows, f"{shop}: 正式表缺失"
            row = rows[shop]
            actual_company = formal_sheet.cell(row, headers["公司名称"]).value
            actual_code = formal_sheet.cell(row, headers["统一社会信用代码"]).value
            assert normalize_identity(actual_company) == normalize_identity(qualification.get("company_name")), f"{shop}: 正式主体与平台营业执照不一致"
            assert str(actual_code or "").strip().upper() == str(qualification.get("credit_code") or "").strip().upper(), f"{shop}: 信用代码与平台营业执照不一致"
            assert formal_sheet.cell(row, headers["主体一致性"]).value == "平台营业执照已确认", f"{shop}: 主体一致性状态错误"
    qualifications = json.loads(qualifications_path.read_text(encoding="utf-8")) if qualifications_path.exists() else {}
    enrichment_path = job_dir / "company_enrichment.json"
    enrichment = json.loads(enrichment_path.read_text(encoding="utf-8")) if enrichment_path.exists() else {}
    for row in actual_rows:
        shop = formal_sheet.cell(row, headers["店铺名"]).value
        qualification = qualifications.get(shop, {})
        actual_company = formal_sheet.cell(row, headers["公司名称"]).value or ""
        actual_code = formal_sheet.cell(row, headers["统一社会信用代码"]).value or ""
        if not valid_qualification(qualification):
            confirmed = selected_company(enrichment, shop) if not qualification else {}
            expected_company, expected_code = candidate_identity(confirmed)
            assert normalize_identity(actual_company) == normalize_identity(expected_company), f"{shop}: 主体缺少独立闭环证据"
            assert str(actual_code).strip().upper() == expected_code, f"{shop}: 信用代码缺少闭环证据"
            assert formal_sheet.cell(row, headers["主体一致性"]).value != "平台营业执照已确认", f"{shop}: 伪平台资质状态"
            if not expected_company:
                for field in ("法人", "注册地址", "成立日期", "公司电话", "邮箱"):
                    assert not formal_sheet.cell(row, headers[field]).value, f"{shop}: 未确认主体不能携带正式字段"
        for candidate in enrichment.get(shop, []):
            phones, emails = extract_contacts(candidate.get("contact", {}))
            for field, values in (("候选电话（待核验）", phones), ("候选邮箱（待核验）", emails)):
                rendered = str(formal_sheet.cell(row, headers[field]).value or "")
                assert all(value in rendered for value in values.split("；") if value), f"{shop}: 已取得候选联系方式未完整保留"
    for path in job_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".json", ".txt", ".log", ".md", ".py"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not SECRET_PATTERN.search(text), f"possible secret in {path}"
    result = {"ok": True, "category": job["category"], "formal_records": len(actual_rows), "platforms": sorted({row["platform"] for row in formal_records}), "workbook": str(workbook_path), "formula_errors": 0}
    formula_book.close()
    value_book.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--workbook")
    args = parser.parse_args()
    print(json.dumps(verify(args.job_dir, args.workbook), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
