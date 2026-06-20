import os
import json
import csv
from datetime import datetime
import requests
from flask import Flask, request, send_file
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import psycopg2  # Conector do PostgreSQL

# NOVA IMPORTAÇÃO OFICIAL DO GEMINI
from google import genai
from google.genai import types

app = Flask(__name__)

# Configurações Globais via Variáveis de Ambiente na Render
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER")
DATABASE_URL = os.environ.get("DATABASE_URL")

# Inicializa o Twilio Client se as chaves existirem
twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Inicializa o cliente oficial do Gemini
ai_client = None
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
    print("🚀 Nova API do Gemini configurada com sucesso!")
else:
    print("⚠️ AVISO: GEMINI_API_KEY não localizada.")


# ==========================================
#  FUNÇÕES DE SUPORTE AO BANCO DE DADOS
# ==========================================

def obter_conexao_banco():
    if not DATABASE_URL:
        return None
    try:
        uri = DATABASE_URL
        if uri.startswith("postgres://"):
            uri = uri.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(uri, connect_timeout=5)
        return conn
    except Exception as e:
        print(f"⚠️ Erro ao conectar ao banco-vio: {e}")
        return None


def verificar_e_registrar_usuario(id_whatsapp):
    conn = obter_conexao_banco()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        id_limpo = str(id_whatsapp).replace("whatsapp:", "").strip()
        cursor.execute(
            "INSERT INTO usuarios (id_whatsapp) VALUES (%s) ON CONFLICT (id_whatsapp) DO NOTHING;",
            (id_limpo,)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Erro ao registar utilizador no banco: {e}")
        return False


def salvar_transacao_banco(id_whatsapp, tipo, valor, local, categoria, texto_puro):
    id_limpo = str(id_whatsapp).replace("whatsapp:", "").strip()
    verificar_e_registrar_usuario(id_limpo)

    conn = obter_conexao_banco()
    if not conn:
        return False
    try:
        valor_numerico = float(valor)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO transacoes (id_whatsapp, tipo, valor, local, categoria, texto_puro)
               VALUES (%s, %s, %s, %s, %s, %s);""",
            (id_limpo, tipo, valor_numerico, local, categoria, texto_puro)
        )
        conn.commit()
        cursor.close()
        conn.close()
        print("💾 Gravado com sucesso no PostgreSQL!")
        return True
    except Exception as e:
        print(f"❌ Erro ao inserir transação no PostgreSQL: {e}")
        return False


# ==========================================
#  FUNÇÕES DO CORE E INTELIGÊNCIA ARTIFICIAL
# ==========================================

def obter_arquivo_usuario(id_usuario):
    csv_usuario = f"/tmp/financeiro_{id_usuario}.csv"
    if not os.path.exists(csv_usuario):
        with open(csv_usuario, "w", encoding="utf-8") as f:
            f.write("Data,Tipo,Valor,Local/Origem,Categoria\n")
    return csv_usuario


def verificar_se_e_comando_resumo(texto):
    if not ai_client or not texto:
        return False

    palavra_limpa = texto.strip().lower()
    # Atalho direto de hardware/performance: se for estritamente estas palavras, nem gasta API
    if palavra_limpa in ["resumo", "relatorio", "relatório", "contador", "summary"]:
        return True

    prompt = (
        "Atue como um classificador de intenção linguística de alta precisão.\n"
        "Analise a mensagem enviada pelo usuário e determine se ele está solicitando um resumo, relatório, extrato ou balanço de finanças.\n"
        "Se for um lançamento de despesa comum, responda obrigatoriamente false.\n\n"
        f"Mensagem do usuário: \"{texto}\"\n\n"
        "Responda estritamente com um JSON no seguinte formato:\n"
        "{\n"
        '  "e_resumo": true ou false\n'
        "}"
    )
    try:
        resposta = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        texto_limpo = resposta.text.strip().replace(
            "```json", "").replace("```", "").strip()
        dados = json.loads(texto_limpo)
        return dados.get("e_resumo", False)
    except Exception as e:
        print(f"⚠️ Erro na classificação do comando: {e}")
        return palavra_limpa in ["resumo", "relatorio", "relatório", "contador", "summary"]


def inteligencia_universal_gemini(texto_ou_transcricao):
    if not ai_client or not texto_ou_transcricao:
        return "Saída", "", "Desconhecido", "Outros Gastos"

    # CORRIGIDO: Agora injetamos a variável texto_ou_transcricao de forma explícita para a IA ler!
    prompt = (
        "Atue como um analista financeiro de alta precisão. Extraia os dados estruturados da mensagem do usuário fornecida abaixo.\n"
        "Identifique o tipo (Entrada se for ganho/salário, Saída se for gasto/compra), o valor numérico (use ponto como decimal), "
        "o local/estabelecimento e atribua uma categoria com um emoji adequado.\n\n"
        f"Mensagem do usuário: \"{texto_ou_transcricao}\"\n\n"
        "Formatos JSON estrito exigido:\n"
        "{\n"
        '  "tipo": "Entrada" ou "Saída",\n'
        '  "valor": "apenas números (ex: 1.30)",\n'
        '  "local": "Nome do estabelecimento",\n'
        '  "categoria": "Emoji + Nome da Categoria"\n'
        "}"
    )
    try:
        resposta = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        texto_limpo = resposta.text.strip().replace(
            "```json", "").replace("```", "").strip()
        dados = json.loads(texto_limpo)

        return (
            dados.get("tipo", "Saída"),
            dados.get("valor", ""),
            dados.get("local", "Desconhecido"),
            dados.get("categoria", "Outros Gastos")
        )
    except Exception as e:
        print(f"❌ Erro na análise do Gemini: {e}")
        # FALLBACK MANUAL DE EMERGÊNCIA: Tenta extrair número via código simples se a IA falhar
        import re
        valores = re.findall(r"\d+(??:[.,]\d+)?", texto_ou_transcricao)
        valor_descoberto = valores[0].replace(",", ".") if valores else ""
        return "Saída", valor_descoberto, "Não especificado", "🛒 Outros Gastos"


def processar_midia_url(url_midia, mime_type):
    if not ai_client:
        return ""
    eh_audio = "audio" in mime_type
    ext = "ogg" if eh_audio else "jpg"
    arquivo_temp = f"/tmp/temp_twilio_media.{ext}"
    try:
        resposta = requests.get(url_midia, auth=(
            TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
        with open(arquivo_temp, "wb") as f:
            f.write(resposta.content)
        midia_gemini = ai_client.files.upload(file=arquivo_temp)
        prompt = "Transcreva este áudio." if eh_audio else "Analise este recibo."
        resposta_gemini = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[midia_gemini, prompt]
        )
        try:
            ai_client.files.delete(name=midia_gemini.name)
        except Exception:
            pass
        return resposta_gemini.text.strip()
    except Exception as e:
        print(f"❌ Erro ao processar mídia: {e}")
        return ""
    finally:
        if os.path.exists(arquivo_temp):
            os.remove(arquivo_temp)


# ==========================================
#  ROTA: GERADOR E DOWNLOAD DE EXCEL/CSV
# ==========================================

@app.route("/download/<id_usuario>", methods=["GET"])
def download_relatorio(id_usuario):
    csv_path = f"/tmp/extrato_{id_usuario}.csv"
    conn = obter_conexao_banco()
    if conn:
        try:
            cursor = conn.cursor()
            id_com_mais = "+" + \
                id_usuario if not id_usuario.startswith("+") else id_usuario
            id_sem_mais = id_usuario.replace("+", "")

            cursor.execute("""
                SELECT data_transacao, tipo, valor, local, categoria 
                FROM transacoes 
                WHERE id_whatsapp = %s OR id_whatsapp = %s
                ORDER BY data_transacao DESC;
            """, (id_com_mais, id_sem_mais))
            linhas = cursor.fetchall()
            cursor.close()
            conn.close()

            with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(
                    ["Data", "Tipo", "Valor", "Local/Estabelecimento", "Categoria"])
                for row in linhas:
                    data_formatada = row[0].strftime(
                        "%d/%m/%Y %H:%M") if isinstance(row[0], datetime) else row[0]
                    writer.writerow(
                        [data_formatada, row[1], f"{row[2]:.2f}", row[3], row[4]])

            return send_file(csv_path, mimetype="text/csv", as_attachment=True, download_name=f"Vio_Extrato_{id_usuario}.csv")
        except Exception as e:
            return f"Erro ao gerar arquivo: {e}", 500
    return "Banco offline", 500


# ==========================================
#  WEBHOOK DO TWILIO
# ==========================================

@app.route("/webhook", methods=["POST"])
def twilio_webhook():
    remetente = request.values.get("From", "")
    texto_recebido = request.values.get("Body", "").strip()
    url_midia = request.values.get("MediaUrl0", "")
    mime_type = request.values.get("MediaContentType0", "")

    id_usuario = remetente.replace("whatsapp:", "").replace("+", "").strip()

    if url_midia:
        texto_transcrito = processar_midia_url(url_midia, mime_type)
        if texto_transcrito:
            texto_recebido = texto_transcrito

    # INTERCEPTOR: COMANDO DE RESUMO
    if texto_recebido and verificar_se_e_comando_resumo(texto_recebido):
        conn = obter_conexao_banco()
        if conn:
            try:
                cursor = conn.cursor()
                id_com_mais = "+" + \
                    id_usuario if not id_usuario.startswith(
                        "+") else id_usuario
                id_sem_mais = id_usuario.replace("+", "")

                cursor.execute("""
                    SELECT categoria, SUM(valor) 
                    FROM transacoes 
                    WHERE tipo = 'Saída' AND (id_whatsapp = %s OR id_whatsapp = %s)
                    GROUP BY categoria 
                    ORDER BY SUM(valor) DESC;
                """, (id_com_mais, id_sem_mais))

                linhas = cursor.fetchall()
                cursor.close()
                conn.close()

                twiml_resp = MessagingResponse()
                msg = twiml_resp.message()

                if linhas:
                    resposta_texto = "📊 *Vio: Aqui está o teu Resumo Financeiro!*\n\n"
                    total_geral = 0
                    for cat, val in linhas:
                        categoria_nome = cat if cat else "Outros/Não Categorizado"
                        resposta_texto += f"• {categoria_nome}: *{val:.2f} €*\n"
                        total_geral += val
                    resposta_texto += f"\n💰 *Total acumulado de Saídas:* *{total_geral:.2f} €*"
                    resposta_texto += "\n\n📄 _O arquivo completo em Excel foi consolidado para o teu Contador abaixo!_"

                    msg.body(resposta_texto)
                    host_app = request.host_url.rstrip('/')
                    msg.media(f"{host_app}/download/{id_usuario}")
                else:
                    resposta_texto = "📊 *Vio:* Ainda não encontrei nenhuma despesa registada para o teu número no banco de dados."
                    msg.body(resposta_texto)

                return str(twiml_resp)

            except Exception as e_banco:
                resposta_texto = f"⚠️ *Vio:* Erro ao processar o teu resumo no banco: {e_banco}"
        else:
            resposta_texto = "⚠️ *Vio:* O banco de dados está temporariamente inacessível."

        twiml_resp = MessagingResponse()
        twiml_resp.message(resposta_texto)
        return str(twiml_resp)

    # FLUXO NORMAL: PROCESSAMENTO DE LANÇAMENTOS
    resposta_texto = ""
    if texto_recebido:
        tipo, v, l, c = inteligencia_universal_gemini(texto_recebido)

        if v:
            if not l or l.lower() == "desconhecido":
                l = "Não especificado"

            gravou_no_banco = salvar_transacao_banco(
                id_whatsapp=id_usuario, tipo=tipo, valor=v, local=l, categoria=c, texto_puro=texto_recebido
            )

            if not gravou_no_banco:
                try:
                    csv_usuario = obter_arquivo_usuario(id_usuario)
                    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M")
                    with open(csv_usuario, "a", encoding="utf-8") as f:
                        f.write(f"{data_atual},{tipo},{v},{l},{c}\n")
                except Exception as e_file:
                    print(f"❌ Erro crítico no Fallback CSV: {e_file}")

            if tipo == "Entrada":
                resposta_texto = f"💰 *Vio:* Entendi: *\"{texto_recebido}\"* -> Entrada de {v} em *({c})*."
            else:
                resposta_texto = f"✅ *Vio:* Entendi: *\"{texto_recebido}\"* -> Despesa de {v} no {l} em *({c})*."
        else:
            resposta_texto = f"⚠️ *Vio:* Entendi: \"{texto_recebido}\", mas não consegui extrair os valores com precisão."
    else:
        resposta_texto = "⚠️ *Vio:* Recebi a tua mensagem, mas não consegui extrair nenhum conteúdo legível."

    twiml_resp = MessagingResponse()
    twiml_resp.message(resposta_texto)
    return str(twiml_resp)


@app.route("/")
def index():
    return "Bot Vio Ativo e Operando via Twilio na Render com Proteção Híbrida!"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
