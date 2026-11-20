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
- **Assinatura:** cada versão nova só é instalada se estiver assinada pela chave que mora no chip de segurança do Mac de quem publica. Mesmo que alguém tome a conta do GitHub, não consegue empurrar uma versão falsa.
  - Criar a chave (uma vez): **Logger > Chave de Assinatura…**. A parte pública vai para `mac/UPDATE_KEY`.
  - Assinar: **Logger > Assinar Versão Nova…**, colar o link do `latest.json` da versão, conferir e confirmar com Touch ID ou senha. A assinatura copiada entra com `python3 tools/release.py --sig ASSINATURA`.
  - Se o Mac da chave sumir, instale o Logger de novo pelo instalador e crie outra chave.
- Para publicar uma versão: aumente o número em `VERSION`, rode `python3 tools/release.py "o que mudou"`, assine e envie para o GitHub.

## Sem instalar (modo navegador)
**Abrir Logger.command** ainda funciona: abre o Logger no navegador. Feche a janela do Terminal para encerrar.

## Uso
- **Novo trabalho:** nome do evento + data (depois da meia-noite já sugere a data de ontem). Escolha o modo:
  - **Padrão:** um dia, pastas de sempre, arquivos soltos em cada pasta.
  - **Vários dias / vários cartões:** `AAAA_MM_DD - Dia 1`, `AAAA_MM_DD - Dia 2`… (data na frente, sempre em ordem cronológica), e cada cartão vai numa subpasta `Cartao 01`, `Cartao 02`…
- As pastas (Ronin, Fotos, Áudio, Action, Drone, Fixa Wide) são editáveis e ficam salvas como seu padrão.
- **Backup opcional:** escolha um segundo HD e ele grava nos dois ao mesmo tempo, lendo o cartão uma vez só.
- **Cartões:** aparecem sozinhos quando você pluga. Cada um mostra o tipo reconhecido, quantos arquivos novos tem e para qual pasta vai. Dá para trocar o destino (ex.: Sony → `Fixa Wide`) e ele lembra a escolha para aquele cartão.
- **Para onde vai a Sony (A7 IV e outras):** se o trabalho tem só uma pasta de câmera principal (Ronin, Vídeos, Dj Booth…), a Sony vai direto para ela, vídeos e fotos. Action, Drone, Áudio e Fotos não contam. Com duas ou mais (ex.: Ronin + Fixa Wide), o app pergunta, e a câmera lembra a escolha pelo número de série. Arquivos JRS continuam indo para Ronin. Miniaturas da câmera (pasta `THMBNL`) nunca são copiadas; fotos JPG/ARW do `DCIM` sim.
- **Continuar trabalho existente:** para logar mais cartões num trabalho já criado (ex.: segundo dia).

## Depois da edição

**Renomear drops** (botão *Drops* no topo): com o render terminado no DaVinci, o Logger lê o projeto aberto
(renders da fila, clipes "Drop N" e os marcadores ♪ que o Ghost Junior põe) e mostra a prévia:
`V1-0011_Drop 11.mp4` → `11_Eco Club Cardio.mp4`. Número = ordem na timeline; nome da festa = pasta
`AAAA_MM_DD - Festa`; música do VIOT ganha só o título (sem autor e sem "Extended Mix"). Dá para corrigir
o título na prévia (o Logger lembra) e ligar/desligar o VIOT de um drop. Nada é sobrescrito, e o botão
*Desfazer* volta os nomes do render. Se o render ainda estiver rodando, o Logger fica de olho e avisa.
Precisa do DaVinci aberto com *Preferences > System > General > External scripting using* em **Local**.

**Limpar fotos** (botão *Fotos*): marque as estrelas no Lightroom e escolha a festa. O Logger lê o
catálogo (uma cópia, o Lightroom pode estar aberto), copia as fotos com estrela (RAW e o `.xmp`, se houver)
para `Select Fotos` dentro da festa, confere cada cópia por SHA-256 e nunca sobrescreve um arquivo que já
esteja lá. Só depois manda a pasta Fotos para a **Lixeira** (pelo Finder, dá para recuperar), e só depois de
dois cliques seus. Se uma foto com estrela sumiu do disco ou uma cópia não bater, nada vai para a Lixeira.

## Segurança e conferência
- Cada arquivo é lido do cartão, gravado, relido do HD e comparado por checksum (BLAKE2b) antes de valer.
- Antes de começar ele confere se tem espaço livre suficiente.
- No fim reconfere tudo e grava em `_logs/` o relatório (`.txt`), os detalhes (`.json`) e o manifesto de checksums (`.b2sum`, confere com `b2sum -l 160 -c arquivo.b2sum`).
- Ele lembra tudo que já foi logado de cada cartão (`~/.cardlogger/`). Cartão não formatado: só os arquivos novos são copiados.
- Selo **"pode formatar"** só aparece quando nenhuma gravação ficou só no cartão: nada novo, nenhum arquivo que o Logger não reconhece e todas as cópias conferidas no HD. Se sobrou algo (formato desconhecido, pasta marcada "não copiar", cópia que falhou), o cartão **não é ejetado** e a tela diz o que ficou.
- Se você apagar uma cópia do HD, o cartão volta a oferecer aquele arquivo para copiar de novo. Se o HD da cópia anterior estiver desligado, ele não copia de novo (para não misturar trabalhos) e avisa que não dá para conferir.
- **Parar a cópia:** o botão para depois do arquivo atual; nada fica pela metade. Copiando de novo, ele reconhece o que já estava no HD e não cria duplicados.
- Cada cópia é gravada de verdade no HD (F_FULLFSYNC do macOS) antes de ser conferida e de o cartão ser ejetado. O backup nunca sobrescreve um arquivo que já estava lá.
- O histórico e os ajustes guardam uma cópia de segurança (`.bak`); se um estragar, o Logger volta para ela e avisa.
- O motor só aceita comandos da própria janela do Logger (um site aberto no navegador não consegue mandar nada). Atualizações só instalam se o pacote baixado bater com a impressão digital (SHA-256) publicada.
- Notificação com som quando termina. Botão para ejetar os cartões logados.

## Recursos profissionais
- **Cópia em paralelo:** até 4 cartões copiando ao mesmo tempo, cada um com sua barra.
- **Prefixo da câmera (opcional):** renomeia só a cópia (JR_C0001.MP4, WIDE_C0001.MP4…). Os prefixos de cada pasta são editáveis.
- **Relatório visual:** `Relatorio.html` na pasta do trabalho, com miniatura, duração, resolução, fps e horário de cada clipe, horas gravadas por câmera e linha do tempo de todas as câmeras. Também mostra alertas para arquivo corrompido, clipe com menos de 1 segundo e câmera com o relógio errado. Miniaturas usam as ferramentas do macOS; se tiver o ffmpeg instalado (`brew install ffmpeg`), ficam melhores.
- **Meus HDs (⌘K):** um mapa de cada HD externo, em grade ou lista. Dá para abrir as pastas e ver os arquivos mesmo com o HD desligado, e a busca procura o nome em todos os HDs de uma vez (sem ligar para acento; "eco club jrs0001" acha o JRS0001 da pasta Eco Club).
  - HD novo conectado: o Logger pergunta se quer mapear (Mapear / Agora não / Nunca para este HD). Também dá para clicar em "Mapear" em qualquer HD conectado, ou "Mapear outro HD".
  - HD que já está no mapa: cada vez que você conecta, ele relê sozinho e rápido (só confere o que mudou) e mostra o que entrou e o que saiu. Depois de uma cópia de cartões, o HD do trabalho também é atualizado.
  - Só leitura: o Logger lê nomes, tamanhos e datas e guarda o mapa neste Mac (~/.cardlogger/hds.sqlite). Nada é gravado nos HDs. Se o HD for desconectado no meio da leitura, o mapa anterior fica como estava. Enquanto copia cartões, a leitura espera.
  - "Esquecer" apaga só o mapa guardado no Mac; o HD não é tocado. Pacotes como catálogos do Lightroom (.lrdata) entram como um item só.
  - Trabalhos do catálogo antigo (de HDs ainda não mapeados) continuam aparecendo na busca.
- **Manifesto padrão cinema (ASC MHL, opcional):** cria a pasta `ascmhl` com o checksum de cada arquivo, no formato que produtoras e finalizadoras usam.

## Reconhecimento dos cartões
Fica em `cards.json`. Hoje reconhece pela estrutura de pastas: Sony (`PRIVATE/M4ROOT` ou `M4ROOT`, ou qualquer cartão cujo XML/foto diga Sony), drone DJI (`DCIM/DJI_…` com arquivos `.SRT`), action Osmo/GoPro/Insta360 (Osmo Action usa `DCIM/DJI_…` sem `.SRT`), gravador de áudio (`.WAV`) e "Outra câmera" (qualquer cartão com `DCIM`/`PRIVATE`, vai para `Fixa Wide` por padrão).
Pelo Terminal também roda sem janela: `python3 cardlogger.py "/caminho/do/trabalho" --dry-run`.
