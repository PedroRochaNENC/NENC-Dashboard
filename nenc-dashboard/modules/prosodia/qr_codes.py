"""
NencBoost — QR codes do projeto.

Página própria para o que vivia no expander "Gerenciar QR Codes do Projeto"
da lista de projetos: a lista de QR codes com as entradas de cada um, o
detalhe do QR escolhido (imagem, link, destino, exclusão) e o formulário de
criação, que ocupa o lugar do detalhe.

O texto de verificação é do projeto e entra no link de todos os QR codes.
Antes ele aparecia em três formulários e salvar um sobrescrevia os outros;
agora fica uma vez, no topo da página.
"""

import html

import pandas as pd
import streamlit as st
from utils import auth, ui
from utils.icons import material, page_title

user = auth.require_module("prosodia")

from utils import prosodia_db
from utils import prosodia_summary as summary
from utils.organization_data import list_external_resources
from utils.project_ui import active_project
from utils.whatsapp_api_client import (
    build_whatsapp_deeplink,
    create_api_project,
    create_project_qr_code,
    delete_project_qr_code,
    generate_qr_code_bytes,
    get_project_participations,
    get_project_qr_codes,
    is_configured,
    suggest_next_qr_code,
    update_project_qr_code,
)

prosodia_db.init_db()
project = active_project(prosodia_db, "pros_project_id", "modules/prosodia/projetos.py")
pode_alterar = prosodia_db.user_can_modify_project(project, user)
api_id = project.get("api_project_id")
verification_text = project.get("qr_verification_text") or prosodia_db.DEFAULT_QR_VERIFICATION_TEXT

DEFAULT_WELCOME = (
    "Quero agradecer muito a sua participação. Grave um áudio com o conteúdo que achar importante "
    "(sugestão, reclamação, saudação, agradecimento... ) que possa colaborar com a melhoria do nosso trabalho. "
    "Críticas, sugestões e também elogios serão muito bem vindos."
)
# Último recurso, o número de produção da Cloud API: sem ele o deeplink
# apontaria para lugar nenhum quando a organização ainda não cadastrou seus
# números.
PRODUCTION_PHONE = "551151233587"
STATUS_LABELS = {"active": "Ativo", "inactive": "Inativo", "paused": "Pausado"}
PANEL = "pros_qr_panel"
SELECTED = "pros_qr_selected"
FLASH = "pros_qr_flash"


def _save_project(**changes) -> None:
    """`update_project` regrava todos os campos; repassa os atuais com a mudança."""
    fields = {
        "name": project["name"],
        "especialidade": project.get("especialidade", ""),
        "historico": project.get("historico", ""),
        "problemas": project.get("problemas", ""),
        "questions": project.get("questions", ""),
        "entities": project.get("entities", ""),
        "briefing_filename": project.get("briefing_filename", ""),
        "briefing_text": project.get("briefing_text", ""),
        "whatsapp_campaign_id": project.get("whatsapp_campaign_id"),
        "quality_thresholds": project.get("quality_thresholds"),
        "api_project_id": project.get("api_project_id"),
        "qr_verification_text": project.get("qr_verification_text"),
    }
    fields.update(changes)
    prosodia_db.update_project(project["id"], **fields)


def _digits(value) -> str:
    return "".join(filter(str.isdigit, str(value or "")))


def _org_phones() -> list[str]:
    organization_id = project.get("organization_id") or auth.active_organization_id(user)
    phones = [_digits(p) for p in auth.get_organization_whatsapp_numbers(organization_id) if _digits(p)]
    if not phones:
        for contact in list_external_resources("whatsapp_contact"):
            meta = contact.get("metadata") or {}
            phone = _digits(meta.get("phone") or contact.get("external_id"))
            if phone and phone not in phones:
                phones.append(phone)
    return phones or [PRODUCTION_PHONE]


def _kicker(text: str) -> str:
    return (
        '<div style="font-size:.6rem;letter-spacing:.09em;text-transform:uppercase;'
        'color:var(--nenc-faint);margin-bottom:.15rem">{}</div>'.format(text)
    )


def _field(label: str, value: str) -> None:
    st.markdown(
        _kicker(label) + '<div style="font-size:.8rem;line-height:1.5;margin-bottom:.6rem">{}</div>'.format(
            html.escape(value) if value else '<span style="color:var(--nenc-faint)">—</span>'
        ),
        unsafe_allow_html=True,
    )


def _flash(message: str) -> None:
    st.session_state[FLASH] = message
    st.rerun()


# ---------------------------------------------------------------------------
# Cabeçalho e pré-condições
# ---------------------------------------------------------------------------

ui.inject_theme()
st.page_link("modules/prosodia/resumo.py", label="Voltar para Resumo", icon=":material/arrow_back:")
ui.breadcrumb_nav(
    ("NencBoost", "modules/prosodia/projetos.py"),
    (project["name"], "modules/prosodia/resumo.py"),
    ("QR codes", None),
)

if not is_configured():
    page_title("qr-code", "QR codes", project["name"])
    st.warning("A API de WhatsApp não está configurada. Os QR codes são criados e lidos por ela.")
    st.stop()

if not api_id:
    page_title("qr-code", "QR codes", project["name"])
    st.info("Este projeto ainda não está vinculado à API de WhatsApp, onde os QR codes ficam.")
    if pode_alterar and st.button("Vincular projeto à API", type="primary"):
        try:
            created = create_api_project(project["name"], user.organization_name)
            _save_project(api_project_id=created.get("id"))
        except Exception as error:
            st.error("Erro ao vincular à API: {}".format(error))
        else:
            _flash("Projeto vinculado à API de WhatsApp.")
    st.stop()

try:
    qr_codes = get_project_qr_codes(api_id)
    participations = get_project_participations(api_id)
except Exception as error:
    page_title("qr-code", "QR codes", project["name"])
    st.error("Não foi possível carregar os QR codes da API: {}".format(error))
    st.stop()

phones = _org_phones()
stats = summary.qr_stats(summary.project_entries(project["id"]))


def _stats_for(qr: dict) -> dict:
    # A importação grava o nome do QR, ou o código quando a API não manda nome.
    return stats.get(str(qr.get("name") or "").strip()) or stats.get(str(qr.get("code") or "").strip()) or {}


qr_codes = sorted(qr_codes, key=lambda qr: -_stats_for(qr).get("entradas", 0))
total_entries = sum(_stats_for(qr).get("entradas", 0) for qr in qr_codes)

col_title, col_new = st.columns([4, 1], vertical_alignment="bottom")
with col_title:
    page_title(
        "qr-code",
        "QR codes",
        "{} cadastrados · {} entradas · {} participantes inscritos".format(
            len(qr_codes), summary.number_text(total_entries), len(participations)
        ),
    )
with col_new:
    if pode_alterar:
        st.button("Novo QR code", type="primary", width="stretch",
                  on_click=lambda: st.session_state.__setitem__(PANEL, "novo"))

if st.session_state.get(FLASH):
    st.success(st.session_state.pop(FLASH))

with st.container(border=True):
    c_text, c_phones, c_edit = st.columns([2.2, 2, 0.8], vertical_alignment="center")
    preview = verification_text if len(verification_text) <= 110 else verification_text[:110].rstrip() + "…"
    c_text.markdown(
        _kicker("Texto de verificação · vale para todos")
        + '<div style="font-size:.78rem">"{}"</div>'.format(html.escape(preview)),
        unsafe_allow_html=True,
    )
    c_phones.markdown(
        _kicker("Números de destino da organização")
        + '<div style="font-size:.78rem">{}</div>'.format(" · ".join(summary.phone_text(p) for p in phones)),
        unsafe_allow_html=True,
    )
    with c_edit:
        with st.popover("Editar", disabled=not pode_alterar, width="stretch"):
            st.caption(
                "O texto vai no link de todos os QR codes. Mudá-lo muda o link, e as imagens "
                "já impressas continuam com o texto antigo."
            )
            with st.form("pros_qr_verification"):
                new_text = st.text_area("Texto de verificação", value=verification_text, height=120)
                if st.form_submit_button("Salvar"):
                    _save_project(qr_verification_text=new_text.strip() or None)
                    _flash("Texto de verificação atualizado.")


# ---------------------------------------------------------------------------
# Painéis
# ---------------------------------------------------------------------------

def _new_panel() -> None:
    with st.container(border=True):
        st.markdown('<div style="font-size:1rem;font-weight:600;margin-bottom:.4rem">Novo QR code</div>',
                    unsafe_allow_html=True)
        code = suggest_next_qr_code(api_id, qr_codes)
        with st.form("pros_qr_new"):
            name = st.text_input("Nome do QR code *", placeholder="Ex: Cartaz Recepção - Unidade Central")
            # O código entra no key de propósito: o `value` de um widget só vale
            # na primeira vez que o key aparece, então um key fixo congelava o
            # campo no código de quando a página abriu.
            st.text_input("Código de rastreio", value=code, disabled=True,
                          help="Gerado automaticamente, em sequência.", key="pros_qr_new_code_{}".format(code))
            phone = st.selectbox("WhatsApp destino *", phones, format_func=summary.phone_text)
            description = st.text_area("Descrição do local ou canal", height=80,
                                       placeholder="Ex: Cartaz A3 afixado no balcão de entrada principal")
            welcome = st.text_area("Resposta automática de boas-vindas", value=DEFAULT_WELCOME, height=120,
                                   help="Enviada pelo WhatsApp assim que o participante escaneia o QR code.")
            st.caption("O texto de verificação é o do projeto e vale para todos os QR codes.")
            c_submit, c_cancel = st.columns([1.4, 1])
            submitted = c_submit.form_submit_button("Gerar e cadastrar", type="primary", width="stretch")
            cancelled = c_cancel.form_submit_button("Cancelar", width="stretch")
        if cancelled:
            st.session_state[PANEL] = "detalhe"
            st.rerun()
        if submitted:
            if not name.strip():
                st.error("Informe o nome do QR code.")
                return
            try:
                create_project_qr_code(
                    api_id,
                    name=name.strip(),
                    description=description.strip() or None,
                    code=code,
                    target_phone=phone,
                    welcome_message=welcome.strip() or None,
                    verification_text=verification_text,
                )
            except Exception as error:
                st.error("Erro ao criar o QR code: {}".format(error))
                return
            st.session_state[PANEL] = "detalhe"
            _flash("QR code {} cadastrado.".format(code))


def _detail_panel(qr: dict) -> None:
    info = _stats_for(qr)
    entries = info.get("entradas", 0)
    target = _digits(qr.get("target_phone")) or phones[0]
    link = build_whatsapp_deeplink(target, verification_text, qr.get("code", ""))
    status = str(qr.get("status") or "")

    with st.container(border=True):
        st.markdown(
            '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:.6rem">'
            '<div><div style="font-size:1rem;font-weight:600">{n}</div>'
            '<div style="font-size:.72rem;color:var(--nenc-muted)">Código {c} · criado em {d}</div></div>{s}</div>'.format(
                n=html.escape(str(qr.get("name") or "")),
                c=html.escape(str(qr.get("code") or "")),
                d=str(qr.get("created_at") or "")[:10],
                s=ui.status_chip("qr-code", STATUS_LABELS.get(status, status), tone="accent" if status == "active" else "muted")
                if status else "",
            ),
            unsafe_allow_html=True,
        )

        c_image, c_numbers = st.columns([1, 1.1], vertical_alignment="center")
        png = None
        with c_image:
            try:
                png = generate_qr_code_bytes(link)
                st.image(png, width=150)
            except Exception as error:
                st.error(str(error))
        with c_numbers:
            share = entries / total_entries if total_entries else 0
            peak = info.get("pico")
            st.markdown(
                '<div style="display:flex;flex-direction:column;gap:.7rem">'
                '<div><div style="font-size:1.5rem;font-weight:500;line-height:1">{e}</div>'
                '<div style="font-size:.72rem;color:var(--nenc-muted)">entradas · {p:.0f}% do projeto</div></div>'
                '<div><div style="font-size:.9rem">{h}</div>'
                '<div style="font-size:.72rem;color:var(--nenc-muted)">{hl}</div></div></div>'.format(
                    e=entries, p=share * 100,
                    h="{}h".format(peak) if peak is not None else summary.relative_time(info.get("ultima")) or "—",
                    hl="horário de pico" if peak is not None else "última entrada",
                ),
                unsafe_allow_html=True,
            )

        c_download, c_audios = st.columns(2)
        c_download.download_button(
            "Baixar PNG", data=png or b"", file_name="qrcode_{}.png".format(qr.get("code", "")),
            mime="image/png", disabled=png is None, width="stretch", key="pros_qr_png_{}".format(qr["id"]),
        )
        with c_audios:
            st.page_link("modules/prosodia/entrevistas.py", label="Abrir Áudios", icon=material("list-bullets"))

        st.markdown(_kicker("Link do WhatsApp"), unsafe_allow_html=True)
        st.code(link, language=None, wrap_lines=True)

        if pode_alterar:
            new_target = st.selectbox(
                "WhatsApp destino", phones,
                index=phones.index(target) if target in phones else 0,
                format_func=summary.phone_text,
                help="O número fica gravado no QR code: ao trocar, baixe a imagem de novo e reimprima o material.",
                key="pros_qr_target_{}".format(qr["id"]),
            )
            if st.button("Atualizar destino", disabled=new_target == target, key="pros_qr_target_btn_{}".format(qr["id"])):
                try:
                    update_project_qr_code(api_id, qr["id"], target_phone=new_target)
                except Exception as error:
                    st.error("Erro ao atualizar o destino: {}".format(error))
                else:
                    _flash("Destino atualizado. Baixe a imagem de novo e reimprima o material com este QR code.")
        else:
            _field("WhatsApp destino", summary.phone_text(target))

        _field("Descrição do local", str(qr.get("description") or ""))
        _field("Resposta automática", str(qr.get("welcome_message") or ""))

        if pode_alterar:
            confirm_key = "pros_qr_confirm_{}".format(qr["id"])
            if not st.session_state.get(confirm_key):
                st.button("Excluir QR code", key="pros_qr_del_{}".format(qr["id"]),
                          on_click=lambda: st.session_state.__setitem__(confirm_key, True))
            else:
                st.warning("Excluir **{}**? Quem escanear o material impresso deixa de entrar no projeto.".format(
                    qr.get("name")))
                c_yes, c_no = st.columns(2)
                if c_yes.button("Confirmar exclusão", key="pros_qr_del_yes_{}".format(qr["id"]), width="stretch"):
                    try:
                        delete_project_qr_code(api_id, qr["id"])
                    except Exception as error:
                        st.error("Erro ao excluir: {}".format(error))
                    else:
                        st.session_state.pop(confirm_key, None)
                        st.session_state.pop(SELECTED, None)
                        _flash("QR code excluído.")
                if c_no.button("Cancelar", key="pros_qr_del_no_{}".format(qr["id"]), width="stretch"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()


# ---------------------------------------------------------------------------
# Lista + painel
# ---------------------------------------------------------------------------

if not qr_codes:
    st.info("Nenhum QR code neste projeto ainda.")
    if pode_alterar:
        _new_panel()
    st.stop()

table = pd.DataFrame([
    {
        "id": qr["id"],
        "QR code": qr.get("name") or "",
        "Local": qr.get("description") or "",
        "Código": qr.get("code") or "",
        "Entradas": _stats_for(qr).get("entradas", 0),
        "Última entrada": summary.relative_time(_stats_for(qr).get("ultima")) or "sem entradas",
    }
    for qr in qr_codes
])

col_list, col_panel = st.columns([1.5, 1], gap="medium")

with col_list:
    event = st.dataframe(
        table.drop(columns="id"),
        hide_index=True,
        width="stretch",
        height=min(38 + 35 * len(table), 560),
        on_select="rerun",
        selection_mode="single-row",
        key="pros_qr_table",
        column_config={
            "QR code": st.column_config.TextColumn(width="medium"),
            "Local": st.column_config.TextColumn(width="medium"),
            "Código": st.column_config.TextColumn(width="small"),
            "Entradas": st.column_config.ProgressColumn(
                format="%d", min_value=0, max_value=max(1, int(table["Entradas"].max())),
            ),
        },
    )
    rows = event.selection.rows
    if rows:
        chosen = table.iloc[rows[0]]["id"]
        if chosen != st.session_state.get(SELECTED):
            st.session_state[SELECTED] = chosen
            st.session_state[PANEL] = "detalhe"

with col_panel:
    if st.session_state.get(PANEL) == "novo" and pode_alterar:
        _new_panel()
    else:
        selected_id = st.session_state.get(SELECTED)
        selected = next((qr for qr in qr_codes if qr["id"] == selected_id), qr_codes[0])
        _detail_panel(selected)
