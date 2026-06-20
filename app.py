import os
import json
from datetime import datetime
import requests
from flask import Flask, request
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
DATABASE_URL = os.environ.get("DATABASE_URL")  # Nova variável para o Banco Vio

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
#  FUNÇÕES DE SUPORTE AO BANCO DE DADOS (CORRIGIDO)
# ==========================================

def obter_conexao_banco():
    """Tenta conectar ao PostgreSQL na Render. Retorna None se falhar."""
    if not DATABASE_URL:
        return None
    try:
        # Corrige dinamicamente possíveis variações do protocolo postgres
        uri = DATABASE_URL
        if uri.startswith("postgres://"):
            uri = uri.replace("postgres://", "postgresql://", 1)

        # Conecta forçando o sslmode diretamente na string caso necessário
        conn = psycopg2.connect(uri, connect_timeout=5)
        return conn
    except Exception as e:
        print(f"⚠️ Erro ao conectar ao banco-vio: {e}")
        return None


def verificar_e_registrar_usuario(id_whatsapp):
    """Garante que o utilizador existe na tabela 'usuarios' antes de lançar transações"""
    conn = obter_conexao_banco()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        # Tratamento rigoroso do ID limpando espaços e prefixos comuns
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
    """Tenta salvar no banco de dados. Retorna True se correr bem, False se falhar."""
    # Garante a existência do utilizador limpando o id_whatsapp primeiro
    id_limpo = str(id_whatsapp).replace("whatsapp:", "").strip()
    verificar_e_registrar_usuario(id_limpo)

    conn = obter_conexao_banco()
    if not conn:
        return False
    try:
        # Conversão explícita e segura do valor de texto para Float numérico
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
        print("💾 Gravado com sucesso no PostgreSQL da Render!")
        return True
    except Exception as e:
        print(f"❌ Erro ao inserir transação no PostgreSQL: {e}")
        return False


# ==========================================
#  FUNÇÕES ORIGINAIS DO CORE DO VIO
# ==========================================

def obter_arquivo_usuario(id_usuario):
    csv_usuario = f"/tmp/financeiro_{id_usuario}.csv"
    if not os.path.exists(csv_usuario):
        with open(csv_usuario, "w", encoding="utf-8") as f:
            f.write("Data,Tipo,Valor,Local/Origem,Categoria\n")
    return csv_usuario


def inteligencia_universal_gemini(texto_ou_transcricao):
    if not ai_client:
        return "Saída", "", "Desconhecido", "Outros Gastos"

    prompt = (
        "Atue como um analista financeiro de alta precisão especializado em ler mensagens de usuários, "
        "notificações automáticas de smartphones (como Google Wallet/Carteira do Google, Apple Pay), "
        "notificações de aplicativos de bancos (MB Way, Revolut, Santander, ActivoBank, etc.) e SMS bancários.\n\n"

        f'Texto recebido: "{texto_ou_transcricao}"\n\n'

        "Instruções cruciais de análise:\n"
        "1. Identifique se o texto descreve uma ENTRADA (recebimento, transferência recebida, salário, depósito) "
        "ou uma SAÍDA (compra, pagamento, débito, gasto, levantamento).\n"
        "2. Extraia o VALOR exato. Remova símbolos de moeda (€, $, R$) e limpe espaços. Use sempre o ponto '.' como separador decimal.\n"
        "3. Identifique o LOCAL ou estabelecimento. Ignore o nome do intermediário de pagamento se houver um local claro "
        "(ex: em 'Carteira do Google • Pingo Doce', o local é 'Pingo Doce'). Se for uma transferência entre pessoas, o local é o nome da pessoa.\n"
        "4. Atribua uma CATEGORIA com emoji condizente com o local ou tipo de gasto.\n\n"

        "Extraia os dados estruturados exatamente no formato JSON abaixo, sem formatações adicionais:\n"
        "{\n"
        '  "tipo": "Entrada" ou "Saída",\n'
        '  "valor": "apenas números (ex: 1.30)",\n'
        '  "local": "Nome do estabelecimento, origem ou destino do dinheiro",\n'
        '  "categoria": "Emoji + Nome da Categoria (ex: 🛒 Supermercado, ☕ Café, 💸 Transferência)"\n'
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
        dados = json.loads(resposta.text.strip())

        return (
            dados.get("tipo", "Saída"),
            dados.get("valor", ""),
            dados.get("local", "Desconhecido"),
            dados.get("categoria", "Outros Gastos")
        )
    except Exception as e:
        print(f"❌ Erro na análise do Gemini: {e}")
        return "Saída", "", "Desconhecido", "Outros Gastos"


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

        if eh_audio:
            prompt = "Transcreva este áudio exatamente na língua em que foi falado. Retorne apenas o texto puro."
        else:
            prompt = "Analise este recibo/nota fiscal. Transcreva o que foi gasto, o valor total e o local em uma frase curta."

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
        print(f"❌ Erro ao processar mídia da Twilio: {e}")
        return ""
    finally:
        if os.path.exists(arquivo_temp):
            os.remove(arquivo_temp)


# ==========================================
#  WEBHOOK DO TWILIO COM LOGICA HIBRIDA
# ==========================================

@app.route("/webhook", methods=["POST"])
def twilio_webhook():
    remetente = request.values.get("From", "")
    texto_recebido = request.values.get("Body", "").strip()
    url_midia = request.values.get("MediaUrl0", "")
    mime_type = request.values.get("MediaContentType0", "")

    id_usuario = remetente.replace("whatsapp:", "").strip()

    # ==========================================
    # COMANDO DE RESUMO PARA O CONTADOR
    # ==========================================
    palavra_chave = texto_recebido.lower().strip()
    if palabra_chave in ["resumo", "relatorio", "relatório", "contador"]:
        conn = obter_conexao_banco()
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT categoria, SUM(valor) 
                    FROM transacoes 
                    WHERE tipo = 'Saída' AND id_whatsapp = %s
                    GROUP BY categoria 
                    ORDER BY SUM(valor) DESC;
                """, (id_usuario,))
                linhas = cursor.fetchall()
                cursor.close()
                conn.close()

                if linhas:
                    resposta_texto = "📊 *Vio: Aqui está o teu Resumo Financeiro!*\n\n"
                    total_geral = 0
                    for cat, val in linhas:
                        resposta_texto += f"{cat}: *{val:.2f} €*\n"
                        total_geral += val
                    resposta_texto += f"\n💰 *Total acumulado de Saídas:* *{total_geral:.2f} €*"
                    resposta_texto += "\n\n📄 _O arquivo completo em Excel foi consolidado para o teu Contador!_"
                else:
                    resposta_texto = "📊 *Vio:* Ainda não encontrei nenhuma despesa registada para este número no banco de dados."
            except Exception as e_banco:
                resposta_texto = f"⚠️ *Vio:* Erro ao processar o teu resumo no banco: {e_banco}"
        else:
            resposta_texto = "⚠️ *Vio:* O banco de dados está temporariamente inacessível para gerar relatórios."

        twiml_resp = MessagingResponse()
        twiml_resp.message(resposta_texto)
        return str(twiml_resp)
    # ==========================================

    if url_midia:
        texto_transcrito = processar_midia_url(url_midia, mime_type)
        if texto_transcrito:
            texto_recebido = texto_transcrito

    resposta_texto = ""

    if texto_recebido:
        tipo, v, l, c = inteligencia_universal_gemini(texto_recebido)

        if v:
            if not l or l.lower() == "desconhecido":
                l = "Não especificado"

            gravou_no_banco = salvar_transacao_banco(
                id_whatsapp=id_usuario,
                tipo=tipo,
                valor=v,
                local=l,
                categoria=c,
                texto_puro=texto_recebido
            )

            if not gravou_no_banco:
                try:
                    csv_usuario = obter_arquivo_usuario(id_usuario)
                    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M")
                    with open(csv_usuario, "a", encoding="utf-8") as f:
                        f.write(f"{data_atual},{tipo},{v},{l},{c}\n")
                    print("⬇️ Banco offline. Dado guardado no Fallback CSV Local.")
                except Exception as e_file:
                    print(
                        f"❌ Erro crítico ao salvar arquivo de emergência: {e_file}")

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
