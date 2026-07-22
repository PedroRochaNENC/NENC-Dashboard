# Operacao Segura

## Bootstrap inicial

Antes de iniciar a aplicacao pela primeira vez, defina no ambiente do servidor:

```text
NENC_DB_PATH=/caminho/persistente/nenc-insights.db
NENC_BOOTSTRAP_ORGANIZATION=Organizacao inicial
NENC_BOOTSTRAP_NAME=Nome do administrador
NENC_BOOTSTRAP_EMAIL=admin@example.com
NENC_BOOTSTRAP_PHONE=5511999999999
NENC_BOOTSTRAP_PASSWORD=uma-senha-com-pelo-menos-12-caracteres
```

Inicie o Streamlit e entre com essa conta. O bootstrap so cria a primeira conta
global quando ainda nao existe um administrador global. Remova a senha de
bootstrap do ambiente apos a inicializacao.

## Migracao de banco legado

Pare todas as instancias do Streamlit que usam o banco antes da migracao. A
organizacao proprietaria precisa existir e estar ativa no mesmo SQLite.

Primeiro execute a verificacao sem alterar o banco:

```powershell
py scripts/migrate_legacy_data.py `
  --database C:\dados\nenc-insights.db `
  --organization-id 123
```

Depois aplique a migracao. O comando cria uma copia do banco antes de alterar
qualquer tabela:

```powershell
py scripts/migrate_legacy_data.py `
  --database C:\dados\nenc-insights.db `
  --organization-id 123 `
  --backup-dir C:\dados\backups `
  --apply
```

O comando falha se a organizacao nao existir, estiver inativa ou se a verificacao
final encontrar registros sem organizacao. Em uma falha, restaure o arquivo criado
em `--backup-dir` antes de investigar ou repetir a operacao.

Recursos remotos legados nao sao adotados pelo script: um administrador global
deve selecionar a organizacao proprietaria e registralos na tela de configuracao
da API WhatsApp. Vector stores legados devem ser adotados somente com
`NENC_LEGACY_ORGANIZATION_ID` apontando para a mesma organizacao.

## Recuperacao de administrador

O bootstrap nao deve ser reutilizado para recuperar acesso. Um administrador
global existente deve criar ou redefinir a senha de outro administrador pela tela
de Administracao. Se nao houver nenhum administrador global ativo, a recuperacao
e uma operacao de emergencia:

1. Pare a aplicacao e crie uma copia verificada do SQLite.
2. Use um procedimento administrativo controlado para reativar ou promover uma
   conta existente; registre o motivo, o operador e o horario no processo de
   operacao da plataforma.
3. Inicie a aplicacao, entre com a conta recuperada e redefina senhas/revogue
   sessoes comprometidas.
4. Revise o `audit_log` e mantenha a copia anterior ate concluir a revisao.

Nao altere hashes de senha manualmente nem recoloque credenciais no banco.

## Backup e restauracao

Mantenha `NENC_DB_PATH` em um volume persistente e faca backups consistentes do
SQLite com a aplicacao parada ou usando o mecanismo de backup SQLite do ambiente
de hospedagem. Teste a restauracao periodicamente em uma copia isolada. O banco
inclui usuarios, sessoes, auditoria, dados de Prosodia, estado dos modulos e
identificadores de recursos externos.

## Roteiro de aceitacao manual

Execute estes testes em uma base nao produtiva apos cada deploy relevante:

1. Entre como usuario regular com apenas um modulo e confirme que as demais
   paginas, inclusive URLs diretas, exibem bloqueio de autorizacao.
2. Entre como administrador de organizacao e crie, desative, redefina senha e
   reduza permissoes de um usuario da propria organizacao. Confirme que sessoes
   antigas deixam de funcionar na proxima execucao de pagina.
3. Entre como administrador global, troque a organizacao ativa e confirme que
   projetos, estados de modulo, vector stores e recursos WhatsApp mudam junto.
4. Em cada organizacao, crie um projeto e um recurso remoto de teste. Confirme
   que ele nao aparece nem pode ser solicitado quando a outra organizacao esta
   ativa.
5. Confirme que somente o administrador global pode abrir a configuracao de
   WhatsApp e que as credenciais nao aparecem em telas de usuarios comuns.
