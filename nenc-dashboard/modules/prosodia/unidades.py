"""
NencBoost — Unidades da organização.

A unidade é da organização, não do projeto: a cliente cadastra as unidades
uma vez e todo projeto dela usa a mesma lista. Cada QR code de projeto se liga
a uma unidade (na tela de QR codes), e o áudio herda a unidade do QR por onde
entrou (docs/SPEC_UNIDADES_E_TAGS.md, §3).

Três caminhos para cadastrar: o formulário, a planilha (codigo, nome, cidade,
uf) e a sugestão pelos nomes dos QR codes, que já dizem onde cada cartaz está.
"""

import html

import pandas as pd
import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("prosodia")

from utils import prosodia_db
from utils import prosodia_unidades as pu
from utils.whatsapp_api_client import get_project_qr_codes, is_configured

prosodia_db.init_db()

SELECTED = "pros_un_selected"
PANEL = "pros_un_panel"
FLASH = "pros_un_flash"
QRS = "pros_un_qrs"

# Escrever é de administrador da organização; cadastrar pede também uma
# organização escolhida, porque em "Todas as organizações" não há a quem
# atribuir a unidade nova. Cada função do banco repete as duas guardas.
pode_editar = auth.can_write(user)
organizacao = auth.active_organization_id(user)
pode_cadastrar = pode_editar and bool(organizacao)


def _flash(message: str) -> None:
    st.session_state[FLASH] = message
    st.rerun()


def _plural(n: int, singular: str, plural: str) -> str:
    return "{} {}".format(n, singular if n == 1 else plural)


ui.inject_theme()
ui.breadcrumb("NencBoost", "Unidades")

unidades = prosodia_db.get_unidades()
por_id = {u["id"]: u for u in unidades}

col_title, col_new = st.columns([4, 1], vertical_alignment="bottom")
with col_title:
    page_title(
        "buildings",
        "Unidades",
        "{} · {} ligados".format(
            _plural(len(unidades), "unidade cadastrada", "unidades cadastradas"),
            _plural(sum(u["n_qr"] for u in unidades), "QR code", "QR codes"),
        ),
    )
with col_new:
    if pode_cadastrar:
        st.button("Nova unidade", type="primary", width="stretch",
                  on_click=lambda: st.session_state.__setitem__(PANEL, "nova"))

if st.session_state.get(FLASH):
    st.success(st.session_state.pop(FLASH))
if not pode_editar:
    st.info("Sua conta consulta as unidades, mas não pode alterá-las.")
elif not organizacao:
    st.info(
        "Em \"Todas as organizações\" a lista é só para consulta. "
        "Escolha uma organização para cadastrar unidades."
    )


# ---------------------------------------------------------------------------
# Lista, cadastro e edição
# ---------------------------------------------------------------------------

def _form_unidade(unidade=None) -> None:
    """Formulário de cadastro (sem `unidade`) ou de edição."""
    atual = unidade or {}
    chave = atual.get("id", "nova")
    with st.form("pros_un_form_{}".format(chave)):
        nome = st.text_input("Nome *", value=atual.get("nome") or "")
        c_codigo, c_cidade, c_uf = st.columns([1.2, 1.6, 0.6])
        codigo = c_codigo.text_input(
            "Código interno", value=atual.get("codigo") or "",
            help="O código que a cliente usa nos sistemas dela. Não se repete na organização.",
        )
        cidade = c_cidade.text_input("Cidade", value=atual.get("cidade") or "")
        uf = c_uf.text_input("UF", value=atual.get("uf") or "", max_chars=2)
        if unidade:
            salvar = st.form_submit_button("Salvar", type="primary")
            cancelar = False
        else:
            c_salvar, c_cancelar = st.columns([1.4, 1])
            salvar = c_salvar.form_submit_button("Cadastrar", type="primary", width="stretch")
            cancelar = c_cancelar.form_submit_button("Cancelar", width="stretch")
    if cancelar:
        st.session_state[PANEL] = "detalhe"
        st.rerun()
    if salvar:
        try:
            if unidade:
                prosodia_db.update_unidade(unidade["id"], nome, codigo, cidade, uf)
            else:
                st.session_state[SELECTED] = prosodia_db.create_unidade(nome, codigo, cidade, uf)
        except (ValueError, auth.AuthorizationError) as error:
            st.error(str(error))
            return
        st.session_state[PANEL] = "detalhe"
        _flash("Unidade {} {}.".format(" ".join(nome.split()), "atualizada" if unidade else "cadastrada"))


def _painel_nova() -> None:
    with st.container(border=True):
        st.markdown('<div style="font-size:1rem;font-weight:600;margin-bottom:.4rem">Nova unidade</div>',
                    unsafe_allow_html=True)
        _form_unidade()


def _painel_detalhe(unidade: dict) -> None:
    with st.container(border=True):
        st.markdown(
            '<div style="font-size:1rem;font-weight:600">{}</div>'
            '<div style="font-size:.72rem;color:var(--nenc-muted);margin-bottom:.5rem">{} · {}</div>'.format(
                html.escape(pu.rotulo_unidade(unidade)),
                _plural(unidade["n_qr"], "QR code", "QR codes"),
                _plural(unidade["n_audios"], "áudio", "áudios"),
            ),
            unsafe_allow_html=True,
        )
        if not pode_editar:
            for rotulo, campo in (("Código interno", "codigo"), ("Cidade", "cidade"), ("UF", "uf")):
                st.caption("{}: {}".format(rotulo, unidade.get(campo) or "—"))
            return
        _form_unidade(unidade)

        confirm_key = "pros_un_confirm_{}".format(unidade["id"])
        if not st.session_state.get(confirm_key):
            st.button("Excluir unidade", key="pros_un_del_{}".format(unidade["id"]),
                      on_click=lambda: st.session_state.__setitem__(confirm_key, True))
            return
        st.warning(
            "Excluir **{}**? {} sem unidade, e os {} dela deixam de ter unidade "
            "até o QR code ser ligado de novo.".format(
                unidade["nome"],
                _plural(unidade["n_qr"], "QR code fica", "QR codes ficam"),
                _plural(unidade["n_audios"], "áudio", "áudios"),
            )
        )
        c_yes, c_no = st.columns(2)
        if c_yes.button("Confirmar exclusão", key="pros_un_del_yes_{}".format(unidade["id"]), width="stretch"):
            try:
                prosodia_db.delete_unidade(unidade["id"])
            except (ValueError, auth.AuthorizationError) as error:
                st.error(str(error))
            else:
                st.session_state.pop(confirm_key, None)
                st.session_state.pop(SELECTED, None)
                _flash("Unidade {} excluída.".format(unidade["nome"]))
        if c_no.button("Cancelar", key="pros_un_del_no_{}".format(unidade["id"]), width="stretch"):
            st.session_state.pop(confirm_key, None)
            st.rerun()


def _aba_lista() -> None:
    if not unidades:
        st.info(
            "Nenhuma unidade cadastrada. Cadastre uma por uma, importe a planilha da cliente "
            "ou crie a partir dos nomes dos QR codes."
        )
        if pode_cadastrar and st.session_state.get(PANEL) == "nova":
            _painel_nova()
        return

    linhas = []
    for unidade in unidades:
        linha = {
            "id": unidade["id"],
            "Código": unidade.get("codigo") or "",
            "Nome": unidade["nome"],
            "Cidade": unidade.get("cidade") or "",
            "UF": unidade.get("uf") or "",
            "QR codes": unidade["n_qr"],
            "Áudios": unidade["n_audios"],
        }
        if not organizacao:
            linha["Organização"] = unidade.get("organization_name") or ""
        linhas.append(linha)
    tabela = pd.DataFrame(linhas)

    col_list, col_panel = st.columns([1.5, 1], gap="medium")
    with col_list:
        event = st.dataframe(
            tabela.drop(columns="id"),
            hide_index=True,
            width="stretch",
            height=min(38 + 35 * len(tabela), 560),
            on_select="rerun",
            selection_mode="single-row",
            key="pros_un_table",
            column_config={
                "Código": st.column_config.TextColumn(width="small"),
                "Nome": st.column_config.TextColumn(width="medium"),
                "UF": st.column_config.TextColumn(width="small"),
            },
        )
        rows = event.selection.rows
        if rows:
            escolhida = int(tabela.iloc[rows[0]]["id"])
            if escolhida != st.session_state.get(SELECTED):
                st.session_state[SELECTED] = escolhida
                st.session_state[PANEL] = "detalhe"
    with col_panel:
        if st.session_state.get(PANEL) == "nova" and pode_cadastrar:
            _painel_nova()
        else:
            _painel_detalhe(por_id.get(st.session_state.get(SELECTED)) or unidades[0])


# ---------------------------------------------------------------------------
# Planilha
# ---------------------------------------------------------------------------

def _ler_arquivo(arquivo) -> pd.DataFrame:
    if arquivo.name.lower().endswith(".xlsx"):
        return pd.read_excel(arquivo, dtype=str)
    try:
        return pd.read_csv(arquivo, sep=None, engine="python", dtype=str, encoding="utf-8-sig")
    except UnicodeDecodeError:
        arquivo.seek(0)
        return pd.read_csv(arquivo, sep=None, engine="python", dtype=str, encoding="latin-1")


def _aba_planilha() -> None:
    st.caption(
        "CSV ou Excel com as colunas codigo, nome, cidade e uf. A unidade com o mesmo código é "
        "atualizada; sem código, a de mesmo nome que ainda não tem código, como as criadas pelos "
        "QR codes. As demais são criadas."
    )
    arquivo = st.file_uploader(
        "Planilha de unidades", type=["csv", "xlsx"], disabled=not pode_cadastrar, key="pros_un_planilha"
    )
    if arquivo is None:
        return
    try:
        registros = _ler_arquivo(arquivo).to_dict("records")
    except Exception as error:
        st.error("Não foi possível ler a planilha: {}".format(error))
        return
    lidas, erros = pu.ler_planilha(registros)
    for erro in erros:
        st.warning(erro)
    if not lidas:
        return
    plano = pu.planejar_importacao(lidas, unidades)
    st.dataframe(
        pd.DataFrame([
            {
                "Ação": "Atualizar" if item["acao"] == pu.ATUALIZAR else "Criar",
                "Código": item["codigo"] or "",
                "Nome": item["nome"],
                "Cidade": item["cidade"] or "",
                "UF": item["uf"] or "",
            }
            for item in plano
        ]),
        hide_index=True,
        width="stretch",
    )
    if st.button("Importar {}".format(_plural(len(lidas), "unidade", "unidades")), type="primary",
                 disabled=not pode_cadastrar, key="pros_un_importar"):
        try:
            contagem = prosodia_db.importar_unidades(lidas)
        except (ValueError, auth.AuthorizationError) as error:
            st.error(str(error))
            return
        _flash("Planilha importada: {} e {}.".format(
            _plural(contagem["criadas"], "unidade criada", "unidades criadas"),
            _plural(contagem["atualizadas"], "atualizada", "atualizadas"),
        ))


# ---------------------------------------------------------------------------
# Sugestão pelos QR codes
# ---------------------------------------------------------------------------

def _buscar_qrs() -> None:
    """Lê da API os QR codes dos projetos da organização ativa.

    Fica num botão, e não na abertura da página, porque são uma chamada à
    API por projeto, e as abas do Streamlit rodam todas a cada render.
    """
    qrs, falhas = [], []
    for projeto in prosodia_db.get_projects():
        if not projeto.get("api_project_id"):
            continue
        try:
            lista = get_project_qr_codes(projeto["api_project_id"])
        except Exception as error:
            falhas.append("{}: {}".format(projeto["name"], error))
            continue
        qrs.extend(
            dict(qr, project_id=projeto["id"], project_name=projeto["name"]) for qr in lista
        )
    st.session_state[QRS] = {"organizacao": organizacao, "qrs": qrs, "falhas": falhas}


def _aba_qrs() -> None:
    if not is_configured():
        st.info("A API de WhatsApp não está configurada, e é nela que os QR codes ficam.")
        return
    if not organizacao:
        st.info("Escolha uma organização para ver os QR codes dos projetos dela.")
        return
    st.caption(
        "Cada QR code ainda sem unidade vira uma proposta, com o nome do QR. Edite os nomes antes "
        "de confirmar: QR codes com o mesmo nome formam uma unidade só, e o nome de uma unidade já "
        "cadastrada liga o QR code a ela."
    )
    st.button("Buscar QR codes sem unidade", on_click=_buscar_qrs, disabled=not pode_cadastrar,
              key="pros_un_buscar")
    lidos = st.session_state.get(QRS)
    if not lidos or lidos.get("organizacao") != organizacao:
        return
    for falha in lidos["falhas"]:
        st.warning("Não foi possível ler os QR codes de {}".format(falha))

    projetos = {qr["project_id"] for qr in lidos["qrs"]}
    vinculados = [
        (project_id, codigo)
        for project_id in projetos
        for codigo in prosodia_db.get_qr_unidades(project_id)
    ]
    propostas = pu.propor_pelos_qrs(lidos["qrs"], vinculados, unidades)
    if not propostas:
        if lidos["qrs"]:
            st.success("Todos os QR codes dos projetos da organização já estão ligados a uma unidade.")
        else:
            st.info("Os projetos da organização não têm QR codes na API.")
        return

    editada = st.data_editor(
        pd.DataFrame([
            {
                "Ligar": True,
                "Projeto": proposta["project_name"],
                "Código": proposta["qr_code"],
                "QR code": proposta["qr_name"],
                "Unidade": proposta["nome"],
            }
            for proposta in propostas
        ]),
        hide_index=True,
        width="stretch",
        disabled=["Projeto", "Código", "QR code"],
        key="pros_un_sugestoes",
        column_config={
            "Ligar": st.column_config.CheckboxColumn(width="small"),
            "Código": st.column_config.TextColumn(width="small"),
            "Unidade": st.column_config.TextColumn(width="medium", required=True),
        },
    )
    if not st.button("Criar e ligar", type="primary", disabled=not pode_cadastrar, key="pros_un_criar"):
        return
    confirmadas = [
        dict(proposta, nome=linha["Unidade"])
        for proposta, linha in zip(propostas, editada.to_dict("records"))
        if linha["Ligar"]
    ]
    criadas = ligados = 0
    erros = []
    for grupo in pu.agrupar_sugestoes(confirmadas, unidades):
        try:
            unidade_id = grupo["unidade_id"]
            if unidade_id is None:
                unidade_id = prosodia_db.create_unidade(grupo["nome"])
                criadas += 1
            for project_id, codigo in grupo["qrs"]:
                prosodia_db.set_qr_unidade(project_id, codigo, unidade_id)
                ligados += 1
        except (ValueError, auth.AuthorizationError) as error:
            erros.append("{}: {}".format(grupo["nome"], error))
    if erros:
        for erro in erros:
            st.error(erro)
        st.caption("O resto foi gravado: {} e {}.".format(
            _plural(criadas, "unidade criada", "unidades criadas"),
            _plural(ligados, "QR code ligado", "QR codes ligados"),
        ))
        return
    _flash("{} e {}.".format(
        _plural(criadas, "unidade criada", "unidades criadas"),
        _plural(ligados, "QR code ligado", "QR codes ligados"),
    ))


aba_lista, aba_planilha, aba_qrs = st.tabs(["Lista", "Importar planilha", "Sugerir pelos QR codes"])
with aba_lista:
    _aba_lista()
with aba_planilha:
    _aba_planilha()
with aba_qrs:
    _aba_qrs()
