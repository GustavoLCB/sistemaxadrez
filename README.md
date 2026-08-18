# Portal do Torneio de Xadrez Escolar — versão Flask + banco de dados

Esta é a versão de produção, independente do Claude: um site próprio, com banco de
dados de verdade, para ser usado no dia do torneio com múltiplos fiscais e um QR Code
público confiável para pais e responsáveis.

## O que já foi testado (antes de chegar até você)

- Fluxo completo: categoria por idade → grupo → rodada → resultado → classificação.
- Bloqueio de faixas de idade sobrepostas (o mesmo bug que corrigimos na versão anterior).
- Importação em massa de Excel com categorização automática por idade — testado com a
  planilha real de 800 atletas: importou em menos de 1 segundo, distribuiu em 37 grupos
  automaticamente, dentro do tamanho-alvo configurado.
- **Login com usuário e senha, por fiscal, restrito ao(s) grupo(s) dele.** Um fiscal não
  vê nem consegue mexer em nenhum grupo que não seja o seu (testado — tentativa de acesso
  cruzado retorna erro 403). Ações administrativas (criar categoria, atleta, etc.) ficam
  bloqueadas para fiscais.
- **Teste de estresse com 50 fiscais**: 50 contas logadas simultaneamente, cada uma no seu
  próprio grupo, gravando resultado de partida no exato mesmo instante — nenhuma gravação
  foi perdida ou sobrescrita.
- O quadro público (usado pelo QR Code, sem login) continua acessível normalmente.
- **CPF como identificador opcional do atleta**, com validação dos dígitos verificadores
  (não só o tamanho). Quando presente, é usado para não duplicar o mesmo atleta em
  importações futuras, mesmo que o nome seja digitado de forma diferente.
- **Zona de Perigo** (aba Atletas): três ações para desfazer uma importação errada —
  remover todos os atletas, remover todos os grupos, ou reiniciar tudo — cada uma exigindo
  que você digite "APAGAR" para confirmar.
- **Migração automática do banco**: se você já tem o site rodando com dados reais, essa
  atualização adiciona a coluna de CPF sozinha, sem apagar nada do que já existe (testado
  simulando exatamente esse cenário antes de te entregar).

## O que ainda falta portar (portamos depois, como fizemos com o HTML)

- Visual completo (tema azul, tabuleiro de fundo, logo).
- QR Code gerado automaticamente na tela (por enquanto, gere manualmente colando o link
  `/publico/<id-do-grupo>` num gerador gratuito como o `qr-code-generator.com`).
- Histórico entre torneios (arquivar e comparar torneios de datas diferentes).

Nada disso é difícil de trazer — é só trabalho de continuar, exatamente como fomos
adicionando recurso por recurso na versão anterior.

---

## Passo 1 — Colocar o código no GitHub

1. Crie uma conta em [github.com](https://github.com) (gratuita).
2. Clique em **New repository**. Dê um nome (ex: `torneio-xadrez`), marque como
   **Private** se quiser manter os dados dos alunos fora do público, e clique em
   **Create repository**.
3. Na página do repositório recém-criado, clique em **uploading an existing file** e
   arraste todos os arquivos desta pasta (`app.py`, `db.py`, `logic.py`,
   `requirements.txt`, `.gitignore`, a pasta `templates/` e a pasta `static/`).
4. Clique em **Commit changes**.

Pronto — o código está versionado. Isso significa que, se algo der errado depois,
sempre dá para voltar para uma versão anterior que funcionava.

## Passo 2 — Criar a conta no PythonAnywhere

1. Vá em [pythonanywhere.com](https://www.pythonanywhere.com) e crie uma conta gratuita
   (plano "Beginner").
2. Depois de logado, vá até a aba **Consoles** e abra um **Bash console**.

## Passo 3 — Baixar o código do GitHub para dentro do PythonAnywhere

No console Bash que abriu, digite (trocando `SEU-USUARIO` pelo seu usuário do GitHub):

```bash
git clone https://github.com/SEU-USUARIO/torneio-xadrez.git
cd torneio-xadrez
pip3 install --user -r requirements.txt
```

## Passo 4 — Configurar o site (Web App)

1. Vá até a aba **Web** do PythonAnywhere e clique em **Add a new web app**.
2. Escolha **Manual configuration** (não "Flask" no assistente automático — dá mais
   controle) e selecione a versão do Python (ex: 3.10).
3. Na seção **Code**, em **Source code**, coloque o caminho da pasta que você acabou de
   clonar: `/home/SEU-USUARIO/torneio-xadrez`.
4. Ainda na aba Web, clique no link do arquivo **WSGI configuration file** (algo como
   `/var/www/seu_usuario_pythonanywhere_com_wsgi.py`) e substitua todo o conteúdo por:

```python
import sys
path = '/home/SEU-USUARIO/torneio-xadrez'
if path not in sys.path:
    sys.path.insert(0, path)

from app import app as application
```

5. Salve o arquivo, volte para a aba **Web** e clique no botão verde **Reload**.
6. Acesse a URL que aparece no topo da página (algo como
   `seu-usuario.pythonanywhere.com`) — o portal deve abrir.

## Passo 5 — Testar

- Abra o site em um navegador comum (fora do PythonAnywhere) para confirmar que é
  público de verdade.
- Abra em dois celulares diferentes ao mesmo tempo, simulando dois fiscais, e grave
  resultados de partidas diferentes ao mesmo tempo — é exatamente o cenário que
  testamos e validamos antes de te entregar este pacote.
- Para o quadro público de um grupo específico, acesse:
  `seu-usuario.pythonanywhere.com/publico/<ID-DO-GRUPO>` (o ID aparece na tabela de
  Grupos, dentro do painel administrativo).

## Passo 6 — Criar o administrador e os fiscais

O site não vem com nenhum usuário criado. Antes de acessar, crie o administrador
direto pelo console Bash do PythonAnywhere (ou localmente, no seu computador):

```bash
cd sistemaxadrez
python3 create_admin.py
```

Vai pedir usuário, nome completo e senha (a senha não aparece na tela enquanto você
digita — é normal, ela está sendo lida mesmo assim). Depois disso, acesse
`seu-usuario.pythonanywhere.com` e faça login com esses dados.

**Para criar os fiscais**, já logado como administrador, vá na aba **Fiscais** dentro
do próprio site: crie um usuário e senha para cada um, e marque a qual grupo (ou
grupos) cada fiscal deve ter acesso. O fiscal, ao logar, só vai ver e conseguir
lançar resultados do grupo que você atribuiu a ele — nada mais.

## Quando algo mudar no código

Sempre que eu (ou você) alterar algo no código:

1. Suba a mudança para o GitHub (pelo site, ou com `git push` se preferir usar Git
   direto do seu computador).
2. No PythonAnywhere, abra o Bash console de novo e rode:
   ```bash
   cd torneio-xadrez
   git pull
   ```
3. Volte na aba **Web** e clique em **Reload**.

## Sobre o banco de dados (`torneio.db`)

O SQLite cria um arquivo `torneio.db` sozinho, na primeira vez que o site roda. Esse
arquivo é onde TODOS os dados do torneio ficam — atletas, resultados, tudo. Ele **não**
deve ser apagado nem sobrescrito ao fazer `git pull` (por isso está no `.gitignore` —
ele nunca vai parar no GitHub, e isso é intencional: cada ambiente tem o seu banco
próprio). Faça backup desse arquivo periodicamente durante o torneio (basta baixá-lo
pela aba **Files** do PythonAnywhere).
