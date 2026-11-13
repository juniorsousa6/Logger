# Logger

Cria a pasta do trabalho e descarrega os cartões nela, só copiando (nada no cartão é apagado, movido ou renomeado).

## Instalar no Mac
1. Descompacte o pacote e dê duplo clique em **Instalar Logger.command** (na primeira vez: botão direito > Abrir).
2. Se o Mac pedir as Ferramentas de Linha de Comando da Apple (grátis), aceite, espere terminar e rode o instalador de novo.
3. O **Logger.app** vai para Aplicativos e abre sozinho, com janela própria e ícone no Dock. Pode apagar a pasta baixada.

O instalador monta o app no próprio Mac, então ele sai certo para a versão do macOS e o processador desta máquina.

## Atualizações
- O Logger confere no GitHub (juniorsousa6/Logger) se tem versão nova toda vez que abre, e também em **Logger > Procurar Atualizações…**. Com um clique ele baixa, refaz o app e reabre.
- Se o macOS for atualizado, o Logger percebe na próxima abertura e oferece se refazer para a versão nova do sistema.
- Atualizar nunca mexe nas suas configurações, nos cartões lembrados nem no histórico (`~/.cardlogger`).
- Para publicar uma versão: aumente o número em `VERSION`, rode `python3 tools/release.py "o que mudou"` e envie para o GitHub.

## Sem instalar (modo navegador)
**Abrir Logger.command** ainda funciona: abre o Logger no navegador. Feche a janela do Terminal para encerrar.

## Uso
- **Novo trabalho:** nome do evento + data (depois da meia-noite já sugere a data de ontem). Escolha o modo:
  - **Padrão:** um dia, pastas de sempre, arquivos soltos em cada pasta.
  - **Vários dias / vários cartões:** `AAAA_MM_DD - Dia 1`, `AAAA_MM_DD - Dia 2`… (data na frente, sempre em ordem cronológica), e cada cartão vai numa subpasta `Cartao 01`, `Cartao 02`…
- As pastas (Ronin, Fotos, Áudio, Action, Drone, Fixa Wide) são editáveis e ficam salvas como seu padrão.
- **Backup opcional:** escolha um segundo HD e ele grava nos dois ao mesmo tempo, lendo o cartão uma vez só.
- **Cartões:** aparecem sozinhos quando você pluga. Cada um mostra o tipo reconhecido, quantos arquivos novos tem e para qual pasta vai. Dá para trocar o destino (ex.: Sony → `Fixa Wide`) e ele lembra a escolha para aquele cartão.
- **Continuar trabalho existente:** para logar mais cartões num trabalho já criado (ex.: segundo dia).

## Segurança e conferência
- Cada arquivo é lido do cartão, gravado, relido do HD e comparado por checksum (BLAKE2b) antes de valer.
- Antes de começar ele confere se tem espaço livre suficiente.
- No fim reconfere tudo e grava em `_logs/` o relatório (`.txt`), os detalhes (`.json`) e o manifesto de checksums (`.b2sum`, confere com `b2sum -l 160 -c arquivo.b2sum`).
- Ele lembra tudo que já foi logado de cada cartão (`~/.cardlogger/`). Cartão não formatado: só os arquivos novos são copiados.
- Selo **"tudo logado, pode formatar"** aparece quando o cartão não tem nada novo e todos os arquivos dele continuam no HD.
- Notificação com som quando termina. Botão para ejetar os cartões logados.

## Recursos profissionais
- **Cópia em paralelo:** até 4 cartões copiando ao mesmo tempo, cada um com sua barra.
- **Prefixo da câmera (opcional):** renomeia só a cópia (JR_C0001.MP4, WIDE_C0001.MP4…). Os prefixos de cada pasta são editáveis.
- **Relatório visual:** `Relatorio.html` na pasta do trabalho, com miniatura, duração, resolução, fps e horário de cada clipe, horas gravadas por câmera e linha do tempo de todas as câmeras. Também mostra alertas para arquivo corrompido, clipe com menos de 1 segundo e câmera com o relógio errado. Miniaturas usam as ferramentas do macOS; se tiver o ffmpeg instalado (`brew install ffmpeg`), ficam melhores.
- **Buscar trabalhos:** digite cliente, evento ou nome de arquivo e ele diz em qual HD está, mesmo com o HD desligado. "Catalogar um HD" inclui os trabalhos antigos (só lê nomes, não altera nada).
- **Manifesto padrão cinema (ASC MHL, opcional):** cria a pasta `ascmhl` com o checksum de cada arquivo, no formato que produtoras e finalizadoras usam.

## Reconhecimento dos cartões
Fica em `cards.json`. Hoje reconhece pela estrutura de pastas: Sony (`PRIVATE/M4ROOT`), drone DJI (`DCIM/DJI_…` com arquivos `.SRT`), action Osmo/GoPro/Insta360 (Osmo Action usa `DCIM/DJI_…` sem `.SRT`), gravador de áudio (`.WAV`) e "Outra câmera" (qualquer cartão com `DCIM`/`PRIVATE`, vai para `Fixa Wide` por padrão).
Pelo Terminal também roda sem janela: `python3 cardlogger.py "/caminho/do/trabalho" --dry-run`.
