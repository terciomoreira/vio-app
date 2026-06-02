import sys
import types

# ==============================================================================
# 🚨 EMULAÇÃO MANDATÓRIA DE MÓDULOS REMOVIDOS NO PYTHON 3.14
# ISSO RESOLVE O CRASH DE IMPORTAÇÃO DO PYDUB ('audioop' e 'aifc')
# ==============================================================================
if 'aifc' not in sys.modules:
    sys.modules['aifc'] = types.ModuleType('aifc')

if 'audioop' not in sys.modules:
    # Cria uma emulação leve do audioop para enganar a verificação inicial do pydub
    mock_audioop = types.ModuleType('audioop')
    mock_audioop.error = Exception
    sys.modules['audioop'] = mock_audioop

if 'pyaudioop' not in sys.modules:
    sys.modules['pyaudioop'] = sys.modules['audioop']
# ==============================================================================

import os
import json
from datetime import datetime
import requests
import pydub
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

# Importação correta e estável do Novo SDK do Gemini
import google.genai as genai
from google.genai import types as genai_types

# Inicializa o Flask
app = Flask(__name__)

# Configuração Global da API do Gemini
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
client = None

if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
    print("🚀 Novo SDK do Gemini inicializado com sucesso!")
else:
    print("⚠️ AVISO: GEMINI_API_KEY não localizada nas variáveis de ambiente.")


def obter_arquivo_usuario(numero_whatsapp):
    id_usuario = numero_whatsapp.replace("whatsapp:", "").strip()
    csv_usuario = f"financeiro_{id_usuario}.csv"
    if not os.path.exists(csv_usuario):
        with open(csv_usuario, "w", encoding="utf-8") as f:
            f.write("Data,Tipo,Valor,Local/Origem,Categoria\n")
    return csv_usuario


def inteligência_universal_gemini(texto_ou_transcricao):
    """
    Usa o Gemini como interpretador universal de idiomas para extrair dados financeiros.
    Remove totalmente a necessidade de regex locais ou modelos rígidos do SpaCy.
    """
    if not client:
        return "Saída", "", "Desconhecido", "Outros Gastos"

    prompt = (
        "Analise a seguinte frase sobre finanças (que pode estar em qualquer idioma): "
        f'"{texto_ou_transcricao}"\n\n'
        "Extraia os seguintes dados estruturados exatamente no formato JSON:\n"
        "{\n"
        '  "tipo": "Entrada" (se for ganho/salário/recebimento) ou "Saída" (se for gasto/despesa/compra),\n'
        '  "valor": "apenas os números usando ponto como separador decimal (ex: 24.50)",\n'
        '  "local": "Nome do local, estabelecimento ou origem do dinheiro capitalizado",\n'
        '  "categoria": "Uma categoria adequada com emoji (ex: 🛒 Supermercado, 🍕 Lazer, 🏠 Contas Fixas, 🚗 Transporte, 💰 Ordenado/Ganhos, 📈 Extras)"\n'
        "}\n"
        "Retorne APENAS o JSON puro, sem marcações de markdown (como ```json) ou textos adicionais."
    )

    try:
        resposta = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )
        dados = json.loads(resposta.text.strip())

        tipo = dados.get("tipo", "Saída")
        valor = dados.get("valor", "")
        local = dados.get("local", "Desconhecido")
        categoria = dados.get("categoria", "Outros Gastos")

        return tipo, valor, local, categoria
    except Exception as e:
        print(f"❌ Erro na análise inteligente do Gemini: {e}")
        return "Saída", "", "Desconhecido", "Outros Gastos"


def transcrever_audio_whatsapp(url_audio):
    if not client:
        print("❌ Motor indisponível: Cliente Gemini offline.")
        return ""

    arquivo_ogg = os.path.join(os.getcwd(), "audio_temp.ogg")
    arquivo_wav = os.path.join(os.getcwd(), "audio_temp.wav")

    try:
        print("📥 Fazendo download do áudio do WhatsApp...")
        resposta = requests.get(url_audio)
        with open(arquivo_ogg, "wb") as f:
            f.write(resposta.content)

        # Conversão utilizando os binários globais do sistema instalados pelos buildpacks
        audio = pydub.AudioSegment.from_file(arquivo_ogg, format="ogg")
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export(arquivo_wav, format="wav")

        print("🎙️ Fazendo upload do WAV para o Gemini...")
        audio_file_gemini = client.files.upload(file=arquivo_wav)

        resposta_gemini = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=[
                "Transcreva este áudio exatamente na língua em que foi falado. "
                "Retorne única e exclusivamente o texto transcrito puro.",
                audio_file_gemini
            ]
        )

        texto_transcrito = resposta_gemini.text.strip()
        print(f"🎯 Transcrição universal concluída: {texto_transcrito}")

        try:
            client.files.delete(name=audio_file_gemini.name)
        except Exception:
            pass

        return texto_transcrito

    except Exception as e:
        print(f"❌ Falha crítica interna no processamento de áudio: {e}")
        return ""
    finally:
        for f_temp in [arquivo_ogg, arquivo_wav]:
            if os.path.exists(f_temp):
                os.remove(f_temp)


def escanear_recibo_gemini(url_imagem):
    if not client:
        return ""
    arquivo_img = os.path.join(os.getcwd(), "temp_recibo.jpg")
    try:
        resposta = requests.get(url_imagem)
        with open(arquivo_img, "wb") as f:
            f.write(resposta.content)

        foto_gemini = client.files.upload(file=arquivo_img)

        prompt = (
            "Analise este recibo de forma universal. Transcreva o que foi gasto, "
            "o valor total e o local em uma frase corta."
        )

        resposta_gemini = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=[prompt, foto_gemini]
        )

        try:
            client.files.delete(name=foto_gemini.name)
        except Exception:
            pass

        return resposta_gemini.text.strip()

    except Exception as e:
        print(f"❌ Erro ao escanear nota fiscal: {e}")
        return ""
    finally:
        if os.path.exists(arquivo_img):
            os.remove(arquivo_img)


@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    remetente = request.values.get("From", "")
    csv_usuario = obter_arquivo_usuario(remetente)

    texto_recebido = request.values.get("Body", "").strip()
    num_midias = int(request.values.get("NumMedia", 0))
    tipo_midia = request.values.get("MediaContentType0", "")

    resposta_twilio = MessagingResponse()
    msg = resposta_twilio.message()

    if num_midias > 0:
        url_midia = request.values.get("MediaUrl0", "")
        if url_midia:
            if "audio" in tipo_midia or "ogg" in url_midia:
                texto_recebido = transcrever_audio_whatsapp(url_midia)
            elif "image" in tipo_midia:
                texto_recebido = escanear_recibo_gemini(url_midia)

    if texto_recebido:
        # Processamento universal baseado no Gemini (independente de idioma)
        tipo, v, l, c = inteligência_universal_gemini(texto_recebido)

        if v and l:
            data_atual = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(csv_usuario, "a", encoding="utf-8") as f:
                f.write(f"{data_atual},{tipo},{v},{l},{c}\n")

            if tipo == "Entrada":
                msg.body(
                    f"💰 *Vio:* Transcrito: *\"{texto_recebido}\"* -> Entrada de {v} em *({c})*.")
            else:
                msg.body(
                    f"✅ *Vio:* Transcrito: *\"{texto_recebido}\"* -> Despesa de {v} no {l} em *({c})*.")
        else:
            msg.body(
                f"⚠️ *Vio:* Entendi: \"{texto_recebido}\", mas não consegui extrair com precisão os valores.")
    else:
        if num_midias > 0:
            if "image" in tipo_midia:
                msg.body(
                    "⚠️ *Vio:* Não consegui ler esta foto. Certifique-se de que está legível.")
            else:
                msg.body(
                    "⚠️ *Vio:* Recebi o seu áudio, mas o interpretador falhou ao processar os arquivos de conversão.")

    return str(resposta_twilio)


if __name__ == "__main__":
    app.run(port=5000)
