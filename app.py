import os
import json
from datetime import datetime
import requests
from flask import Flask, request, jsonify

# IMPORTAÇÃO LEVE: Pacote clássico e estável
import google.generativeai as genai

app = Flask(__name__)

# Configurações Globais via Variáveis de Ambiente na Render
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
EVOLUTION_API_URL = os.environ.get(
    "EVOLUTION_API_URL")  # Ex: https://sua-api.com
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY")  # Token global da API
# Nome da sua instância do WhatsApp
INSTANCE_NAME = os.environ.get("INSTANCE_NAME")

# Inicializa o ecossistema do Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    print("🚀 API do Gemini configurada com sucesso!")
else:
    print("⚠️ AVISO: GEMINI_API_KEY não localizada.")


def enviar_mensagem_evolution(numero, texto):
    """Envia uma mensagem de texto de volta ao usuário usando a Evolution API"""
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY or not INSTANCE_NAME:
        print("⚠️ Configurações da Evolution API ausentes.")
        return False

    url = f"{EVOLUTION_API_URL.rstrip('/')}/message/sendText/{INSTANCE_NAME}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "number": numero,
        "text": texto,
        "delay": 1200
    }
    try:
        res = requests.post(url, json=payload, headers=headers)
        return res.status_code in [200, 201]
    except Exception as e:
        print(f"❌ Erro ao enviar mensagem via Evolution: {e}")
        return False


def obter_arquivo_usuario(id_usuario):
    # Na Render, a pasta /tmp funciona de forma persistente enquanto o container estiver ativo
    csv_usuario = f"/tmp/financeiro_{id_usuario}.csv"
    if not os.path.exists(csv_usuario):
        with open(csv_usuario, "w", encoding="utf-8") as f:
            f.write("Data,Tipo,Valor,Local/Origem,Categoria\n")
    return csv_usuario


def inteligencia_universal_gemini(texto_ou_transcricao):
    if not GEMINI_API_KEY:
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
        model = genai.GenerativeModel(
            "gemini-1.5-flash",
            generation_config={"response_mime_type": "application/json"}
        )
        resposta = model.generate_content(prompt)
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


def processar_midia_url(url_midia, eh_audio=False):
    """Baixa a mídia vinda do webhook da Evolution e envia para o Gemini"""
    if not GEMINI_API_KEY:
        return ""

    ext = "ogg" if eh_audio else "jpg"
    arquivo_temp = f"/tmp/temp_evolution_media.{ext}"

    try:
        resposta = requests.get(url_midia)
        with open(arquivo_temp, "wb") as f:
            f.write(resposta.content)

        midia_gemini = genai.upload_file(
            path=arquivo_temp,
            mime_type="audio/ogg" if eh_audio else "image/jpeg"
        )

        model = genai.GenerativeModel("gemini-1.5-flash")

        if eh_audio:
            prompt = "Transcreva este áudio exatamente na língua em que foi falado. Retorne apenas o texto puro."
        else:
            prompt = "Analise este recibo/nota fiscal. Transcreva o que foi gasto, o valor total e o local em uma frase curta."

        resposta_gemini = model.generate_content([prompt, midia_gemini])

        try:
            genai.delete_file(name=midia_gemini.name)
        except Exception:
            pass

        return respuesta_gemini.text.strip()
    except Exception as e:
        print(f"❌ Erro ao processar mídia da Evolution: {e}")
        return ""
    finally:
        if os.path.exists(arquivo_temp):
            os.remove(arquivo_temp)


@app.route("/webhook", methods=["POST"])
def evolution_webhook():
    payload = request.get_json()
    if not payload:
        return jsonify({"status": "ignored", "reason": "No JSON payload"}), 200

    # Verifica o tipo de evento enviado pela Evolution API
    event = payload.get("event")
    if event != "messages.upsert":
        return jsonify({"status": "ignored", "reason": "Not a message event"}), 200

    data = payload.get("data", {})
    key = data.get("key", {})
    from_me = key.get("fromMe", False)

    # Ignora mensagens enviadas pelo próprio bot para não gerar loop infinito
    if from_me:
        return jsonify({"status": "ignored", "reason": "Message from self"}), 200

    remetente = key.get("remoteJid", "")
    message_content = data.get("message", {})

    texto_recebido = ""
    url_midia = ""
    eh_audio = False

    # Extração de Texto Puro
    if "conversation" in message_content:
        texto_recebido = message_content["conversation"]
    elif "extendedTextMessage" in message_content:
        texto_recebido = message_content["extendedTextMessage"].get("text", "")

    # Extração de Mídias (A Evolution envia a URL pronta ou em formato conversível)
    # Nota: Dependendo da sua config da Evolution, se usar Object Storage (S3/Minio), a URL vem em 'mediaUrl'
    elif "audioMessage" in message_content:
        eh_audio = True
        url_midia = data.get("mediaUrl", "")
    elif "imageMessage" in message_content:
        url_midia = data.get("mediaUrl", "")

    # Se houver mídia válida, faz o processamento no Gemini
    if url_midia:
        texto_recebido = processar_midia_url(url_midia, eh_audio=eh_audio)

    if texto_recebido:
        tipo, v, l, c = inteligencia_universal_gemini(texto_recebido)

        if v:
            if not l:
                l = "Não especificado"

            try:
                csv_usuario = obter_arquivo_usuario(remetente.split("@")[0])
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

        # Envia de volta para o usuário usando a API da Evolution
        enviar_mensagem_evolution(remetente, resposta_texto)

    return jsonify({"status": "success"}), 200


@app.route("/")
def index():
    return "Bot Vio Ativo e Operando de Forma Persistente na Render!"


if __name__ == "__main__":
    # A Render exige escuta na porta definida pela variável de ambiente PORT
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
