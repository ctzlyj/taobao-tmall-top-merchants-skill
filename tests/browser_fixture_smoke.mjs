import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [runtimeRoot, adapterFile, outputFile] = process.argv.slice(2);
if (!runtimeRoot || !adapterFile || !outputFile) {
  throw new Error('Usage: node browser_fixture_smoke.mjs <playwright-runtime-root> <company-core.js> <output.json>');
}
const require = createRequire(resolve(runtimeRoot, 'package.json'));
const { chromium } = require('playwright');
const { runCompany } = await import(pathToFileURL(resolve(adapterFile)).href);
const request = {
  url: 'https://www.riskbird.com/ent/fixture.html',
  company: '示例企业有限公司',
  'credit-code': '913100000000000001',
};
const registry = {
  企业名称: request.company, 统一社会信用代码: request['credit-code'],
  工商注册号: '310000000000001', 法定代表人: '示例法人', 注册地址: '示例地址',
};
const pageHtml = (phone, email, extra = '') => `<!doctype html><meta charset="utf-8">
  <table class="xs-descriptions-box">${Object.entries(registry).map(([label, value]) =>
    `<tr><th>${label}</th><td>${value}</td></tr>`).join('')}</table>
  <div class="info-basic-grow">电话：${phone}</div><div class="info-basic-grow">邮箱：${email}</div>
  <footer>网站客服：400-000-0000</footer>${extra}`;
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const checks = [];
let intercepted = 0;
try {
  const context = await browser.newContext({ serviceWorkers: 'block' });
  let html = pageHtml('010-12345678', '暂无');
  await context.route('**/*', async route => {
    intercepted += 1;
    await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
  });
  const page = await context.newPage();
  const rows = await runCompany(page, request, { pause: async () => {} });
  assert.deepEqual(rows[0].phones, ['010-12345678']);
  assert.equal(rows[0].paid_api_calls, 0);
  checks.push('visible_business_phone_excludes_footer');
  html = pageHtml('138****5678', '暂无');
  const masked = await runCompany(page, request);
  assert.equal(masked[0].contact_status, 'masked');
  assert.deepEqual(masked[0].phones, []);
  checks.push('masked_is_not_filled');
  html = pageHtml('暂无', '暂无');
  assert.equal((await runCompany(page, request))[0].contact_status, 'not_disclosed');
  checks.push('explicit_absence');
  for (const [phone, extra, error] of [
    ['010-12345678', '<p>请先登录</p>', /AUTH_REQUIRED/],
    ['010-12345678', '<p>安全验证</p>', /CHALLENGE_REQUIRED/],
    ['会员解锁', '', /CONTACT_ACCESS_REQUIRED/],
    ['点击查看', '', /UNRECOGNIZED_CONTACT/],
  ]) {
    html = pageHtml(phone, '暂无', extra);
    await assert.rejects(runCompany(page, request), error);
    checks.push(error.source);
  }
  html = pageHtml('010-12345678', '暂无');
  await assert.rejects(runCompany(page, { ...request, company: '其他企业' }), /IDENTITY_CONFLICT/);
  checks.push('identity_conflict');
  const output = { status: 'passed', fixture_only: true, network_requests_forwarded: 0,
    intercepted_requests: intercepted, checks, baseline: rows[0] };
  await mkdir(dirname(resolve(outputFile)), { recursive: true });
  await writeFile(outputFile, JSON.stringify(output, null, 2), 'utf8');
  console.log(JSON.stringify({ status: output.status, checks: checks.length, network_requests_forwarded: 0 }));
} finally {
  await browser.close();
}
