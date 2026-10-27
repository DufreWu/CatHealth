// npm install --no-save playwright && npx playwright install chromium
// node tests/e2e.cjs (from project directory)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { spawn } = require('node:child_process');
const { mkdtempSync, rmSync, readFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const assert = require('node:assert/strict');
const temp=mkdtempSync(join(tmpdir(),'pawbase-e2e-'));
const server=spawn(process.env.PYTHON || 'python',['app.py','--demo','--port','18765','--db',join(temp,'demo.db')]);
let browser;
(async()=>{
 await new Promise((resolve,reject)=>{server.stdout.once('data',resolve);server.once('error',reject);server.once('exit',c=>reject(Error('server exited '+c)));});
 const launch={headless:true,args:['--no-sandbox']};
 if(process.env.CHROMIUM_MODULE){const mod=require(process.env.CHROMIUM_MODULE);const bin=mod.default||mod;launch.executablePath=process.env.CHROMIUM_EXECUTABLE || await bin.executablePath();launch.args=bin.args;}
 browser=await chromium.launch(launch);
 const page=await browser.newPage({viewport:{width:1440,height:1100}});
 if(process.env.TEST_CJK_FONT_DIR){
  const dir=process.env.TEST_CJK_FONT_DIR;
  await page.route('**/__test_fonts/**',route=>{
   const path=new URL(route.request().url()).pathname.replace('/__test_fonts/','');
   if(path==='400.css')return route.fulfill({contentType:'text/css',body:readFileSync(join(dir,path),'utf8')+'\nbody {font-family: \"Noto Sans SC\", sans-serif}'});
   return route.fulfill({contentType:'font/woff2',body:readFileSync(join(dir,path))});
  });
  await page.addInitScript(()=>document.addEventListener('DOMContentLoaded',()=>{const link=document.createElement('link');link.rel='stylesheet';link.href='/__test_fonts/400.css';document.head.append(link);}));
 }
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:18765');await page.locator('#products .card').first().waitFor();
 assert.equal(await page.locator('#products .card').count(),7);
 await page.evaluate(()=>document.fonts.ready);
 await page.screenshot({path:'docs/desktop.png',fullPage:true});
 await page.locator('[data-action=select]').nth(0).click();await page.locator('[data-action=select]').nth(1).click();await page.locator('#compare').click();
 await page.locator('#modal .compare-grid .card').first().waitFor();assert.equal(await page.locator('#modal .card').count(),2);await page.locator('#close-modal').click();
 await page.selectOption('#type','wet');await page.waitForFunction(()=>document.querySelector('#count').textContent==='1 条');
 assert(!(await page.locator('#products').innerText()).includes('碳水'));
 assert(await page.locator('#products .verdict .pill').count()>0);
 await page.locator('[data-action=detail]').click();await page.getByText('标签原值 · as-fed %').waitFor();await page.locator('#close-modal').click();
 await page.locator('#reset').click();await page.waitForFunction(()=>document.querySelector('#count').textContent==='7 条');
 await page.locator('#prescription').check();await page.waitForFunction(()=>document.querySelector('#count').textContent==='8 条');
 await page.locator('#search').fill('不存在的商品');await page.waitForFunction(()=>document.querySelector('#count').textContent==='0 条');
 assert((await page.locator('#products').innerText()).includes('没有匹配商品'));
 await page.goto('http://127.0.0.1:18765/methodology');await page.locator('#methodology article').first().waitFor();
 assert((await page.locator('#methodology #verdicts').innerText()).includes('超过法定上限'));assert((await page.locator('#methodology #warnings').innerText()).includes('3.6 g/1000 kcal ME'));
 await page.goto('http://127.0.0.1:18765/review');await page.locator('#review article').first().waitFor();
 assert((await page.locator('#review').innerText()).includes('示例配方 · 异常标签'));
 await page.goto('http://127.0.0.1:18765');await page.locator('#products .card').first().waitFor();
 await page.selectOption('#language-select','EN');
 assert.equal(await page.locator('#count').textContent(),'7 items');assert.equal(await page.evaluate(()=>document.documentElement.lang),'en');
 for(const [sel,text] of [['#mode-note','Demo mode'],['#footer','Not medical advice'],['#products','Add to compare'],['#products','Protein'],['.hero .intro','Compare ingredients']])assert((await page.locator(sel).innerText()).includes(text),sel+' should include '+text);
 assert(!(await page.locator('.hero .intro').innerText()).includes('<br>'));
 await page.selectOption('#language-select','FI');assert((await page.locator('#products').innerText()).includes('Lisää vertailuun'));
 await page.goto('http://127.0.0.1:18765/methodology');await page.locator('#methodology article').first().waitFor();assert((await page.locator('#methodology').innerText()).includes('Proteiini'));
 await page.selectOption('#language-select','CN');assert((await page.locator('#methodology').innerText()).includes('蛋白质'));
 await page.goto('http://127.0.0.1:18765');await page.locator('#products .card').first().waitFor();
 await page.setViewportSize({width:390,height:844});await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:'docs/mobile.png',fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);
 console.log('PASS: Chromium page/API integration, compare, detail, carbohydrates hidden, verdict labels, prescription default, empty search, methodology, review, language switching, mobile width; no JS errors.');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.kill();rmSync(temp,{recursive:true,force:true});});
