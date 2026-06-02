import os
import json
from datetime import datetime
import requests
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

# IMPORTAÇÃO LEVE: Pacote clássico e estável
import google.generativeai as genai

app = Flask(__name__)

# Configurações Globais via Variáveis de Ambiente
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")

# Inicializa o ecossistema do Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    print("🚀 API do Gemini configurada com sucesso!")
else:
    print("⚠️ AVISO: GEMINI_API_KEY não localizada.")


def obter_arquivo_usuario(numero_whatsapp):
    id_usuario = numero_whatsapp.replace("whatsapp:", "").strip()
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
        # Configura o modelo para responder estritamente em JSON nativo
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


def transcrever_audio_whatsapp(url_audio):
    if not GEMINI_API_KEY:
        return ""

    arquivo_ogg = "/tmp/audio_temp.ogg"

    try:
        print("📥 Fazendo download autenticado do áudio do WhatsApp...")
        if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
            resposta = requests.get(url_audio, auth=(
                TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
        else:
            resposta = requests.get(url_audio)

        with open(arquivo_ogg, "wb") as f:
            f.write(resposta.content)

        print("🎙️ Enviando áudio nativo ao Gemini via API Otimizada...")
        audio_file_gemini = genai.upload_file(
            path=arquivo_ogg, mime_type="audio/ogg")

        model = genai.GenerativeModel("gemini-1.5-flash")
        resposta_gemini = model.generate_content([
            "Transcreva este áudio exatamente na língua em que foi falado de forma universal. Retorne apenas o texto puro, sem comentários.",
            audio_file_gemini
        ])

        texto_transcrito = resposta_gemini.text.strip()
        print(f"🎯 Transcrição concluída: {texto_transcrito}")

        try:
            genai.delete_file(name=audio_file_gemini.name)
        except Exception:
            pass

        return texto_transcrito
    except Exception as e:
        print(f"❌ Falha no processamento de áudio: {e}")
        return ""
    finally:
        if os.path.exists(arquivo_ogg):
            os.remove(arquivo_ogg)


def escanear_recibo_gemini(url_imagem):
    if not GEMINI_API_KEY:
        return ""
    arquivo_img = "/tmp/temp_recibo.jpg"
    try:
        if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
            resposta = requests.get(url_imagem, auth=(
                TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
        else:
            resposta = requests.get(url_imagem)

        with open(arquivo_img, "wb") as f:
            f.write(resposta.content)

        foto_gemini = genai.upload_file(path=arquivo_img)
        prompt = "Analise este recibo de forma universal. Transcreva o que foi gasto, o valor total e o local em uma frase curta."

        model = genai.GenerativeModel("gemini-1.5-flash")
        resposta_gemini = model.generate_content([prompt, foto_gemini])

        try:
            genai.delete_file(name=foto_gemini.name)
        except Exception:
            pass

        return resposta_gemini.text.strip()
    except Exception as e:
        print(f"❌ Erro ao escanear imagem: {e}")
        return ""
    finally:
        if os.path.exists(arquivo_img):
            os.remove(arquivo_img)


@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    remetente = request.values.get("From", "")

    texto_recebido = request.values.get("Body", "").strip()
    num_midias = int(request.values.get("NumMedia", 0))
    tipo_midia = request.values.get("MediaContentType0", "")

    resposta_twilio = MessagingResponse()
    msg = resposta_twilio.message()

    try:
        if num_midias > 0:
            url_midia = request.values.get("MediaUrl0", "")
            if url_midia:
                if "audio" in tipo_midia or "ogg" in url_midia or "audio/ogg" in tipo_midia:
                    texto_recebido = transcrever_audio_whatsapp(url_midia)
                elif "image" in tipo_midia:
                    texto_recebido = escanear_recibo_gemini(url_midia)

        if texto_recebido:
            tipo, v, l, c = inteligencia_universal_gemini(texto_recebido)

            # Se tiver valor, o bot processa. Local vazio vira "Não especificado"
            if v:
                if not l:
                    l = "Não especificado"

                # Tentativa de escrita segura num bloco try independente
                try:
                    csv_usuario = obter_arquivo_usuario(remetente)
                    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M")
                    with open(csv_usuario, "a", encoding="utf-8") as f:
                        f.write(f"{data_atual},{tipo},{v},{l},{c}\n")
                except Exception as e_file:
                    print(
                        f"❌ Erro ao salvar arquivo (mas o bot vai responder): {e_file}")

                if tipo == "Entrada":
                    msg.body(
                        f"💰 *Vio:* Entendi: *\"{texto_recebido}\"* -> Entrada de {v} em *({c})*.")
                else:
                    msg.body(
                        f"✅ *Vio:* Entendi: *\"{texto_recebido}\"* -> Despesa de {v} no {l} em *({c})*.")
            else:
                msg.body(
                    f"⚠️ *Vio:* Entendi: \"{texto_recebido}\", mas não consegui extrair os valores com precisão.")
        else:
            msg.body(
                "⚠️ *Vio:* Recebi o seu arquivo de mídia, mas o download ou a interpretação do Vio falhou.")

    except Exception as e_geral:
        # Garante resposta visual imediata no WhatsApp caso ocorra bug em tempo de execução
        msg.body(f"❌ *Erro Interno do Bot:* {str(e_geral)}")

    return str(resposta_twilio)


@app.route("/")
def index():
    return "Bot Otimizado Vio Online e Operante na Vercel!"


if __name__ == "__main__":
    app.run(port=5000)
