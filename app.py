import os
import json
from datetime import datetime
import requests
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client

# NOVA IMPORTAÇÃO OFICIAL DO GEMINI
from google import genai
from google.genai import types

app = Flask(__name__)

# Configurações Globais via Variáveis de Ambiente na Render
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER")

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
        "Analise a seguinte frase sobre finanças: "
        f'"{texto_ou_transcricao}"\n\n'
        "Extraia os seguintes dados estruturados exatamente no formato JSON:\n"
        "{\n"
        '  "tipo": "Entrada" (se for ganho/salário/recebimento) ou "Saída" (se for gasto/despesa/compra),\n'
        '  "valor": "apenas os números usando ponto como separador decimal (ex: 24.50)",\n'
        '  "local": "Nome do local, estabelecimento ou origem do dinheiro capitalizado",\n'
        '  "categoria": "Uma categoria adequada com emoji (ex: 🛒 Supermercado, 🍕 Lazer, 🏠 Contas Fixas, 🚗 Transporte, 💰 Ordenado/Ganhos, 📈 Extras)"\n'
        "}"
    )

    try:
        # Nova chamada oficial utilizando a SDK moderna
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
    """Baixa a mídia vinda do webhook do Twilio usando autenticação básica e envia para o Gemini"""
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

        # Upload utilizando o novo gerenciador de arquivos da SDK estável
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

        return respuesta_gemini.text.strip()
    except Exception as e:
        print(f"❌ Erro ao processar mídia da Twilio: {e}")
        return ""
    finally:
        if os.path.exists(arquivo_temp):
            os.remove(arquivo_temp)


@app.route("/webhook", methods=["POST"])
def twilio_webhook():
    remetente = request.values.get("From", "")
    texto_recebido = request.values.get("Body", "").strip()
    url_midia = request.values.get("MediaUrl0", "")
    mime_type = request.values.get("MediaContentType0", "")

    id_usuario = remetente.replace("whatsapp:", "").strip()

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

            try:
                csv_usuario = obter_arquivo_usuario(id_usuario)
                data_atual = datetime.now().strftime("%Y-%m-%d %H:%M")
                with open(csv_usuario, "a", encoding="utf-8") as f:
                    f.write(f"{data_atual},{tipo},{v},{l},{c}\n")
            except Exception as e_file:
                print(f"❌ Erro ao salvar arquivo: {e_file}")

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
    return "Bot Vio Ativo e Operando via Twilio na Render!"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
