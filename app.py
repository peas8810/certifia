
import os, io, sqlite3, hashlib, datetime as dt, json, zipfile
from typing import Optional, List
import streamlit as st
from fpdf import FPDF
from PIL import Image
import qrcode
import requests

APP_TITLE = "Sistema de Certificados - NICE / Alfa Unipac"
DB_PATH = "certificados.db"
SECRET_SALT = st.secrets.get("SECRET_SALT", "troque-este-segredo-em-producao")
DEFAULT_BASE_URL = st.secrets.get("BASE_URL", "http://localhost:8501")
TTF_CANDIDATES = [st.secrets.get("TTF_PATH", ""), "DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]

st.set_page_config(page_title="Certificados", layout="wide", page_icon="🎓")

# ------------------------- DB -------------------------
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS certificados(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT, evento TEXT, carga_horaria TEXT, condicao TEXT, instituicao TEXT,
        cidade TEXT, data_evento TEXT, data_emissao TEXT, observacoes TEXT,
        codigo_rastreio TEXT UNIQUE, codigo_originalidade TEXT, qr_url TEXT)""")
    con.commit(); return con

def insert_cert(con, d):
    con.execute("""INSERT INTO certificados(
        nome, evento, carga_horaria, condicao, instituicao, cidade, data_evento, data_emissao, observacoes,
        codigo_rastreio, codigo_originalidade, qr_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (d["nome"], d["evento"], d["carga_horaria"], d["condicao"], d["instituicao"], d["cidade"],
         d["data_evento"], d["data_emissao"], d["observacoes"], d["codigo_rastreio"], d["codigo_originalidade"], d["qr_url"]))
    con.commit()

def find_by_code(con, code: str):
    cur = con.cursor(); cur.execute("SELECT * FROM certificados WHERE codigo_rastreio=?", (code.strip(),))
    row = cur.fetchone()
    if not row: return None
    cols = [c[0] for c in cur.description]; return dict(zip(cols, row))

def export_all(con):
    cur = con.cursor(); cur.execute("SELECT * FROM certificados ORDER BY id DESC")
    rows = cur.fetchall(); cols = [c[0] for c in cur.description]; return cols, rows

# --------------------- Codes/QR -----------------------
def gerar_codigos(payload: str):
    sha = hashlib.sha256((payload + SECRET_SALT).encode("utf-8")).hexdigest()
    codigo_originalidade = sha[:12].upper()
    as_int = int(sha, 16); codigo_rastreio = str(as_int % (10**12)).zfill(12)
    return f"{codigo_rastreio[:6]}.{codigo_rastreio[6:]}", codigo_originalidade

def gerar_qr_bytes(url: str):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_Q, box_size=8, border=2)
    qr.add_data(url); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    b = io.BytesIO(); img.save(b, format="PNG"); return b.getvalue()

# ----------------------- PDF -------------------------
class CertPDF(FPDF):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.use_unicode = False
        for p in TTF_CANDIDATES:
            if p and os.path.exists(p):
                try:
                    self.add_font("DejaVu", "", p, uni=True); self.add_font("DejaVu", "B", p, uni=True)
                    self.use_unicode = True; break
                except Exception: pass

    def safe(self, s): 
        if self.use_unicode: return s
        try: return s.encode("latin-1", "replace").decode("latin-1")
        except: return ''.join(ch if ord(ch)<256 else '?' for ch in s)

    def header(self):
        self.set_fill_color(30,83,154); self.rect(0,0,297,20,"F")
        self.set_text_color(255,255,255)
        self.set_font("DejaVu" if self.use_unicode else "Helvetica","B",16)
        self.cell(297,10,self.safe("CERTIFICADO"),0,1,"C")

    def footer(self):
        self.set_y(-20); self.set_text_color(80,80,80)
        self.set_font("DejaVu" if self.use_unicode else "Helvetica","",9)
        self.cell(0,10,self.safe("Sistema de Certificados - NICE / Alfa Unipac"),0,0,"C")

    def corpo(self, d, logo=None, qr=None):
        if logo:
            try: self.image(io.BytesIO(logo), x=20, y=25, w=35)
            except: pass
        self.set_xy(20,35); self.set_text_color(60,60,60)
        self.set_font("DejaVu" if self.use_unicode else "Helvetica","B",12)
        self.cell(0,8,self.safe(d["instituicao"]),ln=1)
        self.ln(8); self.set_text_color(30,83,154)
        self.set_font("DejaVu" if self.use_unicode else "Helvetica","B",20)
        self.cell(0,10,self.safe("Declaração de Participação"),ln=1)
        self.set_text_color(0,0,0); self.set_font("DejaVu" if self.use_unicode else "Helvetica","",13)
        texto = (f"Declaramos que {d['nome']} participou como {d['condicao'].lower()} "
                 f"do evento \"{d['evento']}\" realizado por {d['instituicao']}")
        if d.get("cidade"): texto += f", em {d['cidade']}"
        texto += f", no dia {d['data_evento']}. Carga horária: {d['carga_horaria']}."
        if d.get("observacoes"): texto += f" {d['observacoes']}"
        self.multi_cell(0,8,self.safe(texto))
        self.ln(8); self.line(35,self.get_y(),120,self.get_y())
        self.set_font("DejaVu" if self.use_unicode else "Helvetica","B",11)
        self.cell(0,10,self.safe("Coordenador(a) - NICE"),ln=1)
        self.set_font("DejaVu" if self.use_unicode else "Helvetica","",11)
        self.cell(0,8,self.safe(f"Data de Emissão: {d['data_emissao']}"),ln=1)
        self.ln(3); self.rect(20,160,180,36); self.set_xy(26,164)
        self.set_font("DejaVu" if self.use_unicode else "Helvetica","B",12); self.set_text_color(30,83,154)
        self.cell(0,6,self.safe("Validação e Rastreabilidade"),ln=1)
        self.set_text_color(0,0,0); self.set_font("DejaVu" if self.use_unicode else "Helvetica","",11)
        self.set_xy(26,172); self.cell(0,6,self.safe(f"Validation Key: {d['codigo_rastreio']}"),ln=1)
        self.set_xy(26,180); self.cell(0,6,self.safe(f"Originalidade: {d['codigo_originalidade']}"),ln=1)
        if qr: self.image(io.BytesIO(qr), x=210, y=155, w=60)

def make_pdf(d, logo_file):
    logo_bytes = logo_file.read() if logo_file else None
    qr_bytes = gerar_qr_bytes(d["qr_url"])
    pdf = CertPDF(orientation="L", unit="mm", format="A4")
    pdf.add_page(); pdf.corpo(d, logo_bytes, qr_bytes)
    out = pdf.output(dest="S")
    return bytes(out) if isinstance(out, (bytearray, memoryview)) else out

# ----------------- Google Sheets Integration -----------------
def post_to_sheets(endpoint_url: str, d: dict) -> bool:
    """Envia certificado para Apps Script (Google Sheets). Espera JSON e resposta 200/ok."""
    try:
        r = requests.post(endpoint_url, json=d, timeout=10)
        return r.status_code == 200
    except Exception:
        return False

# ----------------------- UI: Single --------------------------
def ui_generate_single(con):
    st.subheader("Gerar Certificado (Único)")
    with st.form("form_single"):
        nome = st.text_input("Nome do participante*")
        evento = st.text_input("Nome do evento*")
        carga = st.text_input("Carga horária*", "4 horas")
        cond = st.selectbox("Condição*", ["Participante","Palestrante","Organizador(a)","Mediador(a)","Comissão"])
        inst = st.text_input("Instituição ofertante*","Centro Universitário AlfaUnipac")
        cidade = st.text_input("Cidade/UF (opcional)","Teófilo Otoni/MG")
        data_ev = st.date_input("Data do evento*", value=dt.date.today(), format="DD/MM/YYYY")
        obs = st.text_area("Observações (opcional)")
        logo = st.file_uploader("Logo (opcional)", type=["png","jpg","jpeg"])
        base = st.text_input("Base URL de verificação", value=DEFAULT_BASE_URL)
        col1, col2 = st.columns(2)
        with col1:
            use_gs = st.checkbox("Salvar também no Google Sheets (Apps Script)")
        with col2:
            endpoint = st.text_input("Endpoint do Apps Script (POST)", placeholder="https://script.google.com/macros/s/AKfy.../exec" if use_gs else "", disabled=not use_gs)
        submitted = st.form_submit_button("📄 Gerar Certificado")

    if submitted:
        if not all([nome,evento,carga,cond,inst]):
            st.warning("Preencha todos os campos obrigatórios."); return
        data_em = dt.date.today().strftime("%d/%m/%Y"); data_ev_str = data_ev.strftime("%d/%m/%Y")
        payload = "|".join([nome,evento,carga,cond,inst,data_ev_str,data_em])
        cod_r,cod_o = gerar_codigos(payload); url = f"{base}?verificar={cod_r}"
        d = dict(nome=nome,evento=evento,carga_horaria=carga,condicao=cond,instituicao=inst,cidade=cidade,
                 data_evento=data_ev_str,data_emissao=data_em,observacoes=obs,codigo_rastreio=cod_r,
                 codigo_originalidade=cod_o,qr_url=url)
        insert_cert(con,d)
        if use_gs and endpoint.strip():
            ok = post_to_sheets(endpoint.strip(), d)
            st.info("Enviado ao Google Sheets." if ok else "Falha ao enviar ao Google Sheets.")
        pdf = make_pdf(d, logo)
        st.success(f"✅ Certificado gerado! Código: {cod_r}")
        st.download_button("⬇️ Baixar PDF", pdf, f"certificado_{nome.replace(' ','_')}.pdf", "application/pdf")
        st.write(f"Link de verificação: {url}")

# ----------------------- UI: Bulk ----------------------------
def parse_names(text: str) -> List[str]:
    names = [n.strip() for n in text.replace(";", "\n").splitlines() if n.strip()]
    return names

def ui_generate_bulk(con):
    st.subheader("Geração em Massa")
    st.caption("Insira vários nomes (um por linha ou separados por ponto e vírgula) com os mesmos parâmetros.")
    colA, colB = st.columns([2,1])
    with colA:
        names_text = st.text_area("Lista de nomes*", height=200, placeholder="Maria Silva\nJoão Souza\n...")
    with colB:
        logo = st.file_uploader("Logo (opcional)", type=["png","jpg","jpeg"])
    evento = st.text_input("Nome do evento*", key="bulk_evento")
    carga = st.text_input("Carga horária*", "4 horas", key="bulk_carga")
    cond = st.selectbox("Condição*", ["Participante","Palestrante","Organizador(a)","Mediador(a)","Comissão"], key="bulk_cond")
    inst = st.text_input("Instituição ofertante*","Centro Universitário AlfaUnipac", key="bulk_inst")
    cidade = st.text_input("Cidade/UF (opcional)","Teófilo Otoni/MG", key="bulk_cidade")
    data_ev = st.date_input("Data do evento*", value=dt.date.today(), format="DD/MM/YYYY", key="bulk_data")
    obs = st.text_area("Observações (opcional)", key="bulk_obs")
    base = st.text_input("Base URL de verificação", value=DEFAULT_BASE_URL, key="bulk_base")
    c1, c2 = st.columns(2)
    with c1:
        use_gs = st.checkbox("Salvar também no Google Sheets (Apps Script)", key="bulk_gs")
    with c2:
        endpoint = st.text_input("Endpoint do Apps Script (POST)", placeholder="https://script.google.com/macros/s/AKfy.../exec", disabled=not use_gs, key="bulk_ep")

    if st.button("📦 Gerar ZIP com PDFs"):
        nomes = parse_names(names_text)
        if not (nomes and evento and carga and cond and inst):
            st.warning("Preencha lista de nomes e campos obrigatórios."); return
        data_em = dt.date.today().strftime("%d/%m/%Y"); data_ev_str = data_ev.strftime("%d/%m/%Y")

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for nome in nomes:
                payload = "|".join([nome,evento,carga,cond,inst,data_ev_str,data_em])
                cod_r, cod_o = gerar_codigos(payload); url = f"{base}?verificar={cod_r}"
                d = dict(nome=nome, evento=evento, carga_horaria=carga, condicao=cond, instituicao=inst,
                         cidade=cidade, data_evento=data_ev_str, data_emissao=data_em, observacoes=obs,
                         codigo_rastreio=cod_r, codigo_originalidade=cod_o, qr_url=url)
                insert_cert(con, d)
                if use_gs and endpoint.strip():
                    post_to_sheets(endpoint.strip(), d)
                pdf_bytes = make_pdf(d, logo)
                filename = f"certificado_{nome.replace(' ','_')}.pdf"
                zf.writestr(filename, pdf_bytes)

        zip_buf.seek(0)
        st.success(f"✅ Gerados {len(nomes)} certificados.")
        st.download_button("⬇️ Baixar ZIP", zip_buf.getvalue(), file_name="certificados_em_lote.zip", mime="application/zip")

# ----------------------- UI: Verify -------------------------
def ui_verify(con):
    st.subheader("Verificar Autenticidade")
    q = st.query_params.get("verificar", [""])
    code = q[0] if q else ""
    code = st.text_input("Código (######.######)", code)
    if st.button("🔍 Verificar"):
        r = find_by_code(con, code)
        if not r:
            st.error("❌ Código não encontrado neste banco local.")
        else:
            st.success("✅ Certificado autêntico (base local).")
            for k,v in r.items():
                if k!="id": st.write(f"**{k.capitalize()}:** {v}")

# ----------------------- UI: Admin --------------------------
def ui_admin(con):
    st.subheader("Banco de Certificados")
    cols,rows = export_all(con)
    if not rows: st.info("Nenhum certificado emitido ainda."); return
    import pandas as pd
    df = pd.DataFrame(rows, columns=cols)
    st.dataframe(df, use_container_width=True)
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Exportar CSV", csv, "certificados.csv", "text/csv")

# ----------------------- Main -------------------------------
def main():
    con = init_db(); st.title(APP_TITLE)
    st.sidebar.caption("Opcional: defina st.secrets['TTF_PATH'] para fonte Unicode (DejaVuSans.ttf).")
    menu = st.sidebar.radio("Menu", ["Gerar Único","Gerar em Massa","Verificar (local)","Banco"])
    if menu=="Gerar Único": ui_generate_single(con)
    elif menu=="Gerar em Massa": ui_generate_bulk(con)
    elif menu=="Verificar (local)": ui_verify(con)
    else: ui_admin(con)

if __name__ == "__main__": main()
