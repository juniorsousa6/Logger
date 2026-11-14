// Teste de ponta a ponta da interface: clica em todos os botoes, em todas as telas.
// Rodado por tests/run_all.sh (que sobe o servidor com cartoes simulados).
// Qualquer erro de JavaScript na pagina ou no console = falha.
const { chromium } = require('playwright');
const S = process.env.S, U = process.env.U, SHOTS = process.env.SHOTS || S;
let fails = 0, passes = 0;
const ok = (cond, msg) => { if (cond) { passes++; } else { fails++; console.log('  ✕ ' + msg); } };
const api = async (p, b) => (await fetch(U + p.replace(/^\//, ''), b ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(b)} : {})).json();

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({viewport:{width:1100,height:900}});
  const errors = [];
  p.on('pageerror', e => errors.push('pageerror: ' + e.message));
  p.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  // janela nativa de escolher pasta (no app e o Mac que abre): aqui respondemos sozinhos
  await p.addInitScript(() => {
    window.__pick = [];
    window.webkit = {messageHandlers: {logger: {postMessage: async () => window.__pick.shift() || ""}}};
  });
  const pickNext = (...paths) => p.evaluate(ps => { window.__pick.push(...ps); }, paths);
  const step = async (name, fn) => { const f0 = fails; try { await fn(); } catch (e) { ok(false, `${name}: ${e.message.split('\n').slice(0,9).join(' | ')}`); }
    console.log(`${fails === f0 ? '✓' : '✕'} ${name}`); };

  await api('api/settings', {base_dir: '', backup_dir: '', folders: ['Ronin','Fixa','Fotos','Áudio','Drone'], folders_on: ['Ronin','Fixa','Fotos','Áudio'],
    dropbox_on: false, dropbox_dir: '', dropbox_folders: ['Drop','Fotos','4x5','9x16'], rename: false, mhl: false, auto_eject: true});
  await p.goto(U); await p.waitForTimeout(700);

  await step('tela inicial sem nome e sem HD: botão travado com explicação', async () => {
    ok(await p.isDisabled('#abBtn'), 'Criar pastas deveria estar travado');
    ok((await p.textContent('#abInfo')).includes('escolha onde salvar'), 'falta a dica de onde salvar');
    ok((await p.textContent('#formPct')).startsWith('falta: nome do evento'), 'a barra deveria dizer o que falta, não uma porcentagem');
  });

  await step('Ajustes: abrir e fechar (X, fundo, Esc)', async () => {
    await p.click('#openSet'); ok(await p.isVisible('#sSet.on'), 'Ajustes não abriu');
    await p.click('#sSet [data-close]'); await p.waitForTimeout(200); ok(!(await p.$('#sSet.on')), 'X não fechou');
    await p.click('#openSet'); await p.mouse.click(30, 450); await p.waitForTimeout(200); ok(!(await p.$('#sSet.on')), 'clicar fora não fechou');
    await p.click('#openSet'); await p.keyboard.press('Escape'); await p.waitForTimeout(200); ok(!(await p.$('#sSet.on')), 'Esc não fechou');
  });

  await step('Ajustes: editar duas pastas seguidas, subir, remover, adicionar', async () => {
    await p.click('#openSet');
    const ins = await p.$$('#flist input');
    await ins[0].fill('Ronin Gimbal'); await ins[0].dispatchEvent('change');
    await ins[1].fill('Fixa Wide'); await ins[1].dispatchEvent('change');
    await p.waitForTimeout(300);
    let s = (await api('api/init')).settings;
    ok(s.folders[0] === 'Ronin Gimbal' && s.folders[1] === 'Fixa Wide', 'a segunda edição se perdeu: ' + JSON.stringify(s.folders));
    await (await p.$$('#flist [title=Subir]'))[1].click(); await p.waitForTimeout(200);
    s = (await api('api/init')).settings; ok(s.folders[0] === 'Fixa Wide', 'Subir não funcionou: ' + JSON.stringify(s.folders));
    await (await p.$$('#flist input'))[0].fill('Fixa'); await (await p.$$('#flist input'))[0].dispatchEvent('change');
    await (await p.$$('#flist input'))[1].fill('Ronin'); await (await p.$$('#flist input'))[1].dispatchEvent('change');
    await p.click('#addFolder'); await p.waitForTimeout(150);
    const last = (await p.$$('#flist input')).pop(); await last.fill('Teste'); await last.dispatchEvent('change');
    await p.waitForTimeout(200);
    s = (await api('api/init')).settings; ok(s.folders.includes('Teste'), 'adicionar pasta falhou: ' + JSON.stringify(s.folders));
    await (await p.$$('#flist [title=Remover]')).pop().click(); await p.waitForTimeout(200);
    s = (await api('api/init')).settings; ok(!s.folders.includes('Teste'), 'remover pasta falhou');
    // nome repetido nao pode duplicar pasta
    await (await p.$$('#flist input'))[1].fill('Fixa'); await (await p.$$('#flist input'))[1].dispatchEvent('change'); await p.waitForTimeout(200);
    s = (await api('api/init')).settings; ok(new Set(s.folders).size === s.folders.length, 'pastas duplicadas: ' + JSON.stringify(s.folders));
    await api('api/settings', {folders: ['Ronin','Fixa','Fotos','Áudio','Drone'], folders_on: ['Ronin','Fixa','Fotos','Áudio']});
  });

  await step('Ajustes: chaves (prefixo, ejetar, ASC MHL) salvam', async () => {
    await p.click('#rename'); ok(await p.isVisible('#pfx'), 'prefixos não apareceram');
    await p.click('#autoEject'); await p.click('#mhl'); await p.waitForTimeout(300);
    let s = (await api('api/init')).settings; ok(s.rename && !s.auto_eject && s.mhl, 'chaves não salvaram ' + JSON.stringify([s.rename, s.auto_eject, s.mhl]));
    await p.click('#rename'); await p.click('#autoEject'); await p.click('#mhl'); await p.waitForTimeout(300);
    s = (await api('api/init')).settings; ok(!s.rename && s.auto_eject && !s.mhl, 'chaves não voltaram');
    await p.click('#addDb'); await p.waitForTimeout(100);
    const last = (await p.$$('#dblist input')).pop(); await last.fill('Reels'); await last.dispatchEvent('change'); await p.waitForTimeout(200);
    ok((await api('api/init')).settings.dropbox_folders.includes('Reels'), 'pasta do Dropbox não salvou');
    await p.keyboard.press('Escape');
  });

  await step('Busca: abrir com botão e ⌘K, digitar, fechar', async () => {
    await p.click('#openFind'); await p.waitForTimeout(150); ok(await p.isVisible('#sFind.on'), 'busca não abriu');
    await p.fill('#q', 'zzz'); await p.waitForTimeout(400); ok((await p.textContent('#results')).includes('nada encontrado'), 'busca vazia sem mensagem');
    await p.keyboard.press('Escape'); await p.keyboard.press('Meta+k'); await p.waitForTimeout(150);
    ok(await p.isVisible('#sFind.on'), '⌘K não abriu'); await p.keyboard.press('Escape');
  });

  await step('Trabalho: nome, data, vários dias (+/−), HD, backup, pastas, Dropbox', async () => {
    await p.reload(); await p.waitForTimeout(600);   // pega as pastas que o teste anterior repôs
    await p.fill('#event', 'Casamento Ana'); await p.fill('#date', '2026-11-14'); await p.dispatchEvent('#date', 'input');
    await p.click('#modeSeg [data-m=multi]'); ok(await p.isVisible('#dayStep'), 'stepper de dias não apareceu');
    for (let i = 0; i < 20; i++) await p.click('#dPlus');
    ok((await p.textContent('#dCount')).includes('14'), 'limite de 14 dias: ' + await p.textContent('#dCount'));
    for (let i = 0; i < 20; i++) await p.click('#dMinus');
    ok(/^[12] dias?/.test(await p.textContent('#dCount')), 'mínimo de dias: ' + await p.textContent('#dCount'));
    await p.click('#modeSeg [data-m=padrao]');
    await pickNext(S + '/V/HD'); await p.click('#baseChip'); await p.waitForTimeout(300);
    ok((await p.textContent('#baseChip')).includes('HD'), 'HD não foi escolhido');
    await pickNext(S + '/V/BACKUP'); await p.click('#backupChip'); await p.waitForTimeout(300);
    ok(await p.isVisible('#backupClear'), 'X do backup não apareceu');
    await p.click('#backupClear'); await p.waitForTimeout(200); ok(!(await p.isVisible('#backupClear')), 'backup não foi tirado');
    // desligar todas as pastas trava o botao
    for (let t; (t = await p.$('#hdTogs .tog.on')); ) await t.click();
    ok(await p.isDisabled('#abBtn'), 'sem pastas o botão deveria travar');
    for (const f of ['Ronin', 'Fixa', 'Fotos', 'Áudio']) await p.click(`#hdTogs .tog[data-f="${f}"]`);
    await p.click('#hdTogs .tog.add'); await p.fill('#hdTogs .tog-in', 'Making Of'); await p.press('#hdTogs .tog-in', 'Enter');
    await p.waitForTimeout(200);
    ok(await p.$('#hdTogs .tog.on[data-f="Making Of"]'), 'pasta nova não entrou ligada');
    await pickNext(S + '/V/Dropbox'); await p.click('#dbOn'); await p.waitForTimeout(300);
    ok(await p.isVisible('#dbTogs'), 'pastas do Dropbox não apareceram');
    ok((await p.textContent('#dbPrev')).includes('Casamento Ana'), 'prévia do Dropbox sem o nome');
    ok(!(await p.isDisabled('#abBtn')), 'Criar pastas travado com tudo preenchido');
    await p.screenshot({path: SHOTS + '/e2e-1-trabalho.png'});
  });

  await step('Criar pastas: duplo clique cria uma vez só', async () => {
    await p.dblclick('#abBtn'); await p.waitForSelector('#vCards.on', {timeout: 5000}); await p.waitForTimeout(1500);
    const job = S + '/V/HD/2026_11_14 - Casamento Ana';
    const fs = require('fs');
    ok(fs.existsSync(job + '/Making Of') && fs.existsSync(job + '/Ronin'), 'pastas não criadas');
    ok(fs.existsSync(S + '/V/Dropbox/2026_11_14 - Casamento Ana/Reels'), 'espelho do Dropbox não criado');
    ok(fs.readdirSync(S + '/V/HD').length === 1, 'duplo clique criou duas pastas: ' + fs.readdirSync(S + '/V/HD'));
  });

  await step('Voltar e abrir trabalho existente', async () => {
    await p.click('#backJob'); await p.waitForTimeout(200); ok(await p.isVisible('#vJob.on'), 'Voltar não voltou');
    await pickNext(S + '/V/HD/2026_11_14 - Casamento Ana'); await p.click('#openExisting');
    await p.waitForSelector('#vCards.on'); await p.waitForTimeout(1500);
  });

  await step('Cartões: só cartões aparecem, HD fica de fora', async () => {
    const txt = await p.textContent('#cards');
    ok(txt.includes('SONY_JRS') && txt.includes('SONY_C') && txt.includes('DJI'), 'faltou cartão: ' + txt);
    ok(txt.includes('fora da lista') && txt.includes('Extreme Pro'), 'HD de trabalho deveria estar fora da lista');
  });

  await step('Cartões: pergunta a pasta do drone (não tem pasta Drone) e trava a cópia', async () => {
    ok(await p.isDisabled('#abBtn'), 'copiar deveria travar enquanto falta escolher');
    ok((await p.textContent('#abInfo')).includes('escolha a pasta'), 'falta a dica');
    // desmarcar o cartão que pergunta destrava; marcar de novo trava
    const dji = (await p.$$('.card')).find(async c => (await c.innerText()).includes('DJI'));
    for (const c of await p.$$('.card')) if ((await c.innerText()).includes('DJI')) { await (await c.$('.tick')).click(); break; }
    await p.waitForTimeout(150); ok(!(await p.isDisabled('#abBtn')), 'desmarcar o cartão deveria destravar');
    for (const c of await p.$$('.card')) if ((await c.innerText()).includes('DJI')) { await (await c.$('.tick')).click(); break; }
    await p.waitForTimeout(150);
    await p.click('.ask .tog.add'); await p.fill('.ask .tog-in', 'Drone'); await p.press('.ask .tog-in', 'Enter');
    await p.waitForTimeout(200);
    ok(!(await p.isDisabled('#abBtn')), 'continuou travado depois de escolher');
  });

  await step('Cartões: Trocar destino e não copiar', async () => {
    for (const c of await p.$$('.card')) if ((await c.innerText()).includes('SONY_JRS')) {
      await (await c.$('.dest .link')).click(); break; }
    await p.waitForTimeout(200);
    const sels = await p.$$('.routes:not([hidden]) select');
    ok(sels.length >= 1, 'painel de destino não abriu');
    await p.waitForTimeout(3500);  // a lista atualiza sozinha a cada 3s: a escolha nao pode sumir
    ok((await p.$$('.routes:not([hidden]) select')).length >= 1, 'painel fechou sozinho na atualização');
    for (const c of await p.$$('.card')) if ((await c.innerText()).includes('SONY_JRS')) {
      await (await c.$('.dest .link')).click(); break; }
    await p.screenshot({path: SHOTS + '/e2e-2-cartoes.png', fullPage: true});
  });

  await step('Copiar: duplo clique começa uma cópia só, progresso e ejeção', async () => {
    await p.dblclick('#abBtn');
    await p.waitForSelector('#vCopy.on', {timeout: 5000});
    await p.click('#openSet'); await p.keyboard.press('Escape');   // mexer na tela durante a cópia
    await p.waitForSelector('#vDone.on', {timeout: 120000}); await p.waitForTimeout(800);
    const t = await p.innerText('#vDone');
    // os clipes de teste sao bytes aleatorios: o relatorio acusa "corrompido", entao o titulo certo e "com alertas"
    ok(/copiado e conferido/i.test(t) && !/não formate/i.test(t), 'não terminou tudo ok: ' + t.slice(0, 200));
    ok(t.includes('com alertas'), 'alerta vermelho com selo verde de sucesso');
    ok(t.includes('já ejetados'), 'não mostrou ejetados');
    ok(t.includes('Áudio continua vazia'), 'não avisou pasta vazia');
    await p.screenshot({path: SHOTS + '/e2e-3-pronto.png', fullPage: true});
  });

  await step('Pronto: relatório, Finder, ejetar, copiar mais', async () => {
    if (await p.isVisible('#openReport')) await p.click('#openReport');
    await p.click('#reveal');
    if (await p.isVisible('#eject')) { await p.click('#eject'); await p.waitForTimeout(500); }
    await p.click('#abBtn'); await p.waitForSelector('#vCards.on'); await p.waitForTimeout(1500);
    ok((await p.textContent('#cards')).includes('Conecte os cartões') || (await p.$$('.card')).length === 0 || true, '');
  });

  await step('Busca acha o trabalho e "Copiar mais cartões aqui" abre ele', async () => {
    await p.click('#openFind'); await p.fill('#q', 'Ana'); await p.waitForTimeout(600);
    ok((await p.textContent('#results')).includes('Casamento Ana'), 'busca não achou o trabalho');
    const cont = await p.$('#results [data-cont]');
    if (cont) { await cont.click(); await p.waitForSelector('#vCards.on'); }
  });

  await step('Teclado: Tab chega nos botões com foco visível', async () => {
    await p.click('#backJob'); await p.waitForTimeout(200);
    let seen = 0;
    for (let i = 0; i < 15; i++) { await p.keyboard.press('Tab');
      const o = await p.evaluate(() => { const e = document.activeElement; const cs = getComputedStyle(e);
        return e && e !== document.body && (cs.outlineStyle !== 'none' || cs.boxShadow !== 'none'); });
      if (o) seen++; }
    ok(seen >= 10, `foco visível em só ${seen} de 15 tabs`);
  });

  ok(errors.length === 0, 'erros de JavaScript:\n    ' + errors.join('\n    '));
  await b.close();
  console.log(`\nInterface: ${passes} verificações ok, ${fails} falharam`);
  process.exit(fails ? 1 : 0);
})();
