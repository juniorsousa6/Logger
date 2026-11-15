// Tela "Renomear drops": le o DaVinci (simulado com o projeto Eco Club real), corrige um titulo,
// renomeia, confere no disco e desfaz. Rodado por tests/run_all.sh.
const {chromium} = require('playwright');
const fs = require('fs'), path = require('path');
const U = process.env.U, S = process.env.S, SHOTS = process.env.SHOTS || S;
const dir = path.join(S, 'Dropbox/VIOT/2026_11_14 - Eco Club/9x16');
let fails = 0, oks = 0;
const ok = (c, m) => { if (c) oks++; else { fails++; console.log('  ✕ ' + m); } };
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({viewport: {width: 1200, height: 900}, deviceScaleFactor: 2});
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  await p.goto(U); await p.waitForTimeout(600);
  await p.click('#openPos'); await p.waitForSelector('#sPos .drow');
  const txt = await p.textContent('#sPos');
  ok(txt.includes('Renomear 52 arquivos'), 'botão não mostra 52 arquivos');
  ok(txt.includes('Viot - Cardio'), 'não mostrou a música do drop');
  const vals = await p.$$eval('#sPos .djob:first-child input[data-k]', a => a.map(i => i.value));
  ok(vals.includes('Cardio') && vals.includes('Put It Down') && vals.includes('Chove Chuva'), 'títulos do VIOT errados: ' + vals.join(','));
  await p.screenshot({path: path.join(SHOTS, 'drops-1-previa.png')});
  // corrige um titulo e tira o VIOT de outro
  const inp = await p.$('#sPos .djob:first-child input[value="Put It Down"]'); await inp.fill('Put It Down VIP');
  await p.click('#sPos .djob:first-child [data-apply]'); await p.waitForTimeout(800);
  const files = fs.readdirSync(dir).sort();
  ok(files.includes('1_Eco Club.mp4') && files.includes('11_Eco Club Cardio.mp4'), 'não renomeou no disco');
  ok(files.includes('40_Eco Club Put It Down VIP.mp4'), 'correção do título não valeu');
  ok(!files.some(f => f.startsWith('V1-')), 'sobrou arquivo com nome do render');
  ok((await p.textContent('#sPos')).includes('52 renomeados'), 'não avisou o resultado');
  await p.screenshot({path: path.join(SHOTS, 'drops-2-renomeado.png')});
  await p.click('#sPos [data-undo]'); await p.waitForTimeout(800);
  ok(fs.readdirSync(dir).filter(f => f.startsWith('V1-')).length === 50, 'desfazer não voltou os nomes');
  ok(errs.length === 0, 'erro de JavaScript: ' + errs.join(' | '));
  await b.close();
  console.log(`Renomear drops: ${oks} verificações ok, ${fails} falharam`);
  process.exit(fails ? 1 : 0);
})().catch(e => { console.log('  ✕ ' + e.message); process.exit(1); });
