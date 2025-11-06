
import os
import io
import base64
import sqlite3
import hashlib
import datetime as dt
from typing import Optional

import streamlit as st
from fpdf import FPDF
from PIL import Image
import qrcode

# =============================
# ⚙️ Configuração
# =============================
APP_TITLE = "🎓 Sistema de Certificados • NICE / Alfa Unipac"
DB_PATH = os.getenv("CERT_DB_PATH", "certificados.db")
# Coloque um segredo em st.secrets["SECRET_SALT"] ao implantar (Streamlit Cloud: App settings → Secrets)
SECRET_SALT = st.secrets.get("SECRET_SALT", "troque-este-segredo-em-producao")
DEFAULT_BASE_URL = st.secrets.get("BASE_URL", "http://localhost:8501")

st.set_page_config(page_title="Sistema de Certificados", layout="wide", page_icon="🎓")

# =============================
# 🗄️ Banco de Dados (SQLite)
# =============================
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS certificados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            evento TEXT NOT NULL,
            carga_horaria TEXT NOT NULL,
            condicao TEXT NOT NULL,
            instituicao TEXT NOT NULL,
            cidade TEXT,
            data_evento TEXT NOT NULL,
            data_emissao TEXT NOT NULL,
            observacoes TEXT,
            codigo_rastreio TEXT NOT NULL UNIQUE,
            codigo_originalidade TEXT NOT NULL,
            qr_url TEXT NOT NULL
        )
    """)
    con.commit()
    return con

def insert_cert(con, row: dict):
    cur = con.cursor()
    cur.execute("""
        INSERT INTO certificados 
        (nome, evento, carga_horaria, condicao, instituicao, cidade, data_evento, data_emissao, observacoes, 
         codigo_rastreio, codigo_originalidade, qr_url)
        VALUES (:nome, :evento, :carga_horaria, :condicao, :instituicao, :cidade, :data_evento, :data_emissao, :observacoes,
                :codigo_rastreio, :codigo_originalidade, :qr_url)
    """, row)
    con.commit()

def find_by_code(con, code: str) -> Optional[dict]:
    cur = con.cursor()
    cur.execute("SELECT * FROM certificados WHERE codigo_rastreio = ?", (code.strip(),))
    r = cur.fetchone()
    if not r:
        return None
    cols = [c[0] for c in cur.description]
    return dict(zip(cols, r))

def export_all(con):
    cur = con.cursor()
    cur.execute("SELECT * FROM certificados ORDER BY id DESC")
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description]
    return cols, rows

# =============================
# 🔐 Códigos (Rastreio + Originalidade)
# =============================
def humanize_numeric_code(num12: str) -> str:
    """Formata 12 dígitos como '######.######'."""
    return f"{num12[:6]}.{num12[6:12]}"

def gerar_codigos(payload: str):
    # payload: concatenação de campos-chave
    sha = hashlib.sha256((payload + SECRET_SALT).encode("utf-8")).hexdigest()
    # Código de originalidade: 12 hex (48 bits equivalentes) – curto e prático de digitar
    codigo_originalidade = sha[:12].upper()
    # Para o código de rastreio numérico (12 dígitos), use os 12 últimos dígitos do hash base10
    as_int = int(sha, 16)
    num12 = str(as_int % (10**12)).zfill(12)
    codigo_rastreio = humanize_numeric_code(num12)
    return codigo_rastreio, codigo_originalidade

# =============================
# 🧾 Template Profissional (FPDF)
# =============================
class CertPDF(FPDF):
    def header(self):
        # Faixa superior
        self.set_fill_color(30, 83, 154)  # azul institucional
        self.rect(0, 0, 297, 20, "F")     # A4 horizontal: 297 x 210 mm
        # Título
        self.set_xy(0, 8)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 16)
        self.cell(297, 10, "CERTIFICADO", border=0, ln=0, align="C")

    def footer(self):
        self.set_y(-22)
        self.set_draw_color(200, 200, 200)
        self.set_line_width(0.4)
        self.line(20, self.get_y(), 277, self.get_y())
        self.set_y(-18)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(80, 80, 80)
        self.cell(0, 6, "Sistema de Certificados • NICE / Alfa Unipac", 0, 1, "C")

    def corpo(self, dados: dict, logo_bytes: bytes = None, qr_bytes: bytes = None):
        # Margens
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(20, 25, 20)

        # Logo (opcional)
        if logo_bytes:
            try:
                self.image(io.BytesIO(logo_bytes), x=20, y=25, w=35)
            except Exception:
                pass

        # Cabeçalho institucional
        self.set_xy(20, 35)
        self.set_text_color(60, 60, 60)
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 8, dados["instituicao"], ln=1)

        # Título de declaração
        self.ln(6)
        self.set_font("Helvetica", "B", 20)
        self.set_text_color(30, 83, 154)
        self.cell(0, 12, "Declaração de Participação", ln=1)

        # Bloco principal
        self.set_text_color(20, 20, 20)
        self.set_font("Helvetica", "", 13)
        texto = (
            f"Declaramos, para os devidos fins, que "
            f"{dados['nome']} participou como {dados['condicao'].lower()} do evento "
            f"\"{dados['evento']}\" realizado por {dados['instituicao']}"
        )
        if dados.get("cidade"):
            texto += f", em {dados['cidade']}"
        texto += f", no dia {dados['data_evento']}."
        texto += f" Carga horária: {dados['carga_horaria']}."
        if dados.get("observacoes"):
            texto += f" {dados['observacoes']}"

        self.multi_cell(0, 8, texto)
        self.ln(5)

        # Linha de assinatura (exemplo)
        self.set_y(135)
        self.set_draw_color(150, 150, 150)
        self.set_line_width(0.3)
        self.line(35, self.get_y(), 120, self.get_y())
        self.set_y(self.get_y() + 2)
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 6, "Coordenador(a) • NICE", ln=1)

        # Data de emissão
        self.ln(2)
        self.set_font("Helvetica", "", 11)
        self.cell(0, 6, f"Data de Emissão: {dados['data_emissao']}", ln=1)

        # Caixa com códigos
        self.ln(3)
        self.set_draw_color(30, 83, 154)
        self.set_line_width(0.6)
        self.rect(20, 155, 180, 36)
        self.set_xy(26, 160)
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(30, 83, 154)
        self.cell(0, 6, "Validação e Rastreabilidade", ln=1)
        self.set_font("Helvetica", "", 11)
        self.set_text_color(20, 20, 20)
        self.set_xy(26, 168)
        self.cell(0, 6, f"Validation Key: {dados['codigo_rastreio']}", ln=1)
        self.set_xy(26, 176)
        self.cell(0, 6, f"Originalidade: {dados['codigo_originalidade']}", ln=1)

        # QR Code
        if qr_bytes:
            try:
                self.image(io.BytesIO(qr_bytes), x=210, y=150, w=60)
            except Exception:
                pass

# =============================
# 🧩 Utilidades
# =============================
def gerar_qr_png_bytes(url: str) -> bytes:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_Q, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def make_pdf_bytes(dados: dict, logo_file) -> bytes:
    logo_bytes = logo_file.read() if logo_file is not None else None
    qr_bytes = gerar_qr_png_bytes(dados["qr_url"])

    pdf = CertPDF(orientation="L", unit="mm", format="A4")
    pdf.add_page()
    pdf.corpo(dados, logo_bytes=logo_bytes, qr_bytes=qr_bytes)

    out = io.BytesIO()
    pdf_bytes = pdf.output(dest="S").encode("latin-1", "ignore")
    out.write(pdf_bytes)
    out.seek(0)
    return out.read()

def build_payload(nome, evento, carga_horaria, condicao, instituicao, data_evento, data_emissao):
    key = "|".join([nome, evento, carga_horaria, condicao, instituicao, data_evento, data_emissao])
    return key

def guess_base_url():
    # Permite sobrescrever via sidebar; default em st.secrets ou localhost
    return DEFAULT_BASE_URL

# =============================
# 🖥️ Interface
# =============================
def ui_generate(con):
    st.subheader("Gerar Certificado")
    colA, colB = st.columns([2, 1])

    with colA:
        nome = st.text_input("Nome do participante*", value="")
        evento = st.text_input("Nome do evento*", value="")
        carga_horaria = st.text_input("Carga horária*", value="4 horas")
        condicao = st.selectbox("Condição*", ["Participante", "Palestrante", "Organizador(a)", "Mediador(a)", "Comissão"])
        instituicao = st.text_input("Instituição ofertante*", value="Centro Universitário AlfaUnipac")
        cidade = st.text_input("Cidade/UF (opcional)", value="Teófilo Otoni/MG")
        data_evento = st.date_input("Data do evento*", value=dt.date.today(), format="DD/MM/YYYY")
        observacoes = st.text_area("Observações (opcional)", placeholder="Ex.: Registro no Caderno do NICE n° XX, Folha YY...")

    with colB:
        logo = st.file_uploader("Logo (PNG/JPG opcional)", type=["png", "jpg", "jpeg"])
        base_url = st.text_input("Base URL da verificação", value=guess_base_url(), help="Endereço da sua aplicação (ex.: https://meuapp.streamlit.app)")
        st.caption("O QR Code apontará para: base_url + parâmetro ?verificar=CODIGO")

    gerar = st.button("📄 Gerar Certificado (PDF)")

    if gerar:
        # Validações
        obrigatorios = [nome, evento, carga_horaria, condicao, instituicao]
        if not all(obrigatorios):
            st.error("Preencha todos os campos obrigatórios (*) para continuar.")
            return

        data_evento_str = data_evento.strftime("%d/%m/%Y")
        data_emissao_str = dt.date.today().strftime("%d/%m/%Y")

        payload = build_payload(nome, evento, carga_horaria, condicao, instituicao, data_evento_str, data_emissao_str)
        codigo_rastreio, codigo_originalidade = gerar_codigos(payload)
        verify_url = f"{base_url}?verificar={codigo_rastreio}"

        dados = dict(
            nome=nome,
            evento=evento,
            carga_horaria=carga_horaria,
            condicao=condicao,
            instituicao=instituicao,
            cidade=cidade.strip(),
            data_evento=data_evento_str,
            data_emissao=data_emissao_str,
            observacoes=observacoes.strip(),
            codigo_rastreio=codigo_rastreio,
            codigo_originalidade=codigo_originalidade,
            qr_url=verify_url
        )

        # Salva no banco e gera PDF
        try:
            insert_cert(con, dados)
        except sqlite3.IntegrityError:
            # Em caso raríssimo de colisão, regenerar com um pequeno nonce
            payload_nonce = payload + "|" + dt.datetime.now().isoformat()
            codigo_rastreio, codigo_originalidade = gerar_codigos(payload_nonce)
            dados["codigo_rastreio"] = codigo_rastreio
            dados["codigo_originalidade"] = codigo_originalidade
            dados["qr_url"] = f"{base_url}?verificar={codigo_rastreio}"
            insert_cert(con, dados)

        pdf_bytes = make_pdf_bytes(dados, logo)
        st.success(f"Certificado gerado com sucesso! Validation Key: {dados['codigo_rastreio']}")

        st.download_button(
            "⬇️ Baixar PDF do Certificado",
            data=pdf_bytes,
            file_name=f"certificado_{dados['nome'].strip().replace(' ', '_')}.pdf",
            mime="application/pdf"
        )

        st.markdown(f"**Link de verificação (QR Code):** `{dados['qr_url']}`")

def ui_verify(con):
    st.subheader("Verificar Autenticidade")
    query_code = st.query_params.get("verificar", [""])
    if isinstance(query_code, list):
        query_code = query_code[0] if query_code else ""
    code = st.text_input("Digite o Validation Key (formato ######.######)", value=query_code)

    if st.button("🔎 Verificar"):
        if not code.strip():
            st.warning("Informe um código para verificar.")
            return
        r = find_by_code(con, code.strip())
        if not r:
            st.error("❌ Código inválido ou não encontrado.")
        else:
            st.success("✅ Certificado AUTÊNTICO!")
            st.write(f"**Nome:** {r['nome']}")
            st.write(f"**Evento:** {r['evento']}")
            st.write(f"**Condição:** {r['condicao']}")
            st.write(f"**Instituição:** {r['instituicao']}")
            st.write(f"**Data do evento:** {r['data_evento']}")
            st.write(f"**Carga horária:** {r['carga_horaria']}")
            st.write(f"**Originalidade:** {r['codigo_originalidade']}")
            st.write(f"**Data de emissão:** {r['data_emissao']}")
            st.caption("Dica: você pode compartilhar o link desta página com o parâmetro ?verificar=CODIGO para verificação direta.")

def ui_admin(con):
    st.subheader("Banco de Certificados / Exportação")
    cols, rows = export_all(con)
    if not rows:
        st.info("Sem registros ainda.")
        return

    import pandas as pd
    df = pd.DataFrame(rows, columns=cols)
    st.dataframe(df, use_container_width=True, hide_index=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Exportar CSV", data=csv, file_name="certificados_export.csv", mime="text/csv")

# =============================
# 🧭 Navegação
# =============================
def main():
    con = init_db()
    st.title(APP_TITLE)

    st.sidebar.header("Configurações")
    st.sidebar.markdown("• Defina `SECRET_SALT` e `BASE_URL` em **st.secrets** para produção.")
    modo = st.sidebar.radio("Escolha a área", ["Gerar Certificado", "Verificar Código", "Banco/Exportar"])

    if modo == "Gerar Certificado":
        ui_generate(con)
    elif modo == "Verificar Código":
        ui_verify(con)
    else:
        ui_admin(con)

if __name__ == "__main__":
    main()
