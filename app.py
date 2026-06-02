import sys
import types
# ==============================================================================
# 🚨 PROTOCOLO DE INICIALIZAÇÃO ABSOLUTA: LINHAS 1 A 4
# ESTA EMULAÇÃO TEM DE OCORRER ANTES DE QUALQUER OUTRA COMPILAÇÃO DO PYTHON!
# ==============================================================================
sys.modules['aifc'] = types.ModuleType('aifc')

import os
import re
from datetime import datetime
import requests
import spacy
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

# Agora com o aifc emulado em memória, as bibliotecas da Google podem ser lidas
from google import genai
from google.genai import types as genai_types

# Inicializa o Flask
app = Flask(__name__)

# Configuração Global e Estrita da API do Gemini (Puxando a variável do Render)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
client = None

if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
    print("🚀 Novo SDK do Gemini inicializado com sucesso!")
else:
    print("⚠️ AVISO: GEMINI_API_KEY não localizada.")

# Inicialização limpa do modelo SpaCy
try:
    nlp = spacy.load("pt_core_news_sm")
    print("🚀 IA do SpaCy carregada com sucesso a partir do ambiente!")
except Exception as e:
    print(f"❌ Erro ao carregar o SpaCy: {e}. Usando contingência.")
    nlp = None


def obter_arquivo_usuario(numero_whatsapp):
    id_usuario = numero_whatsapp.replace("whatsapp:", "").strip()
    csv_usuario = f"financeiro_{id_usuario}.csv"
    if not os.path.exists(csv_usuario):
        with open(csv_usuario, "w", encoding="utf-8") as f:
            f.write("Data,Tipo,Valor,Local/Origem,Categoria\n")
    return csv_usuario


def analisar_tipo_fluxo(frase_lower):
    ganhos = [
        "recebi", "ganhei", "faturei", "fatura", "salario", "salário", "entrada", "ordenado", "recebido", "credito",
        "ganado", "ingreso", "sueldo", "recibido", "earned", "received", "income", "salary", "deposit"
    ]
    if any(g in frase_lower for g in ganhos):
        return "Entrada"
    return "Saída"


def extrair_valor_universal(frase):
    frase_limpa = re.sub(re.compile(r'[€$£¥₽د.إ]'), '', frase)
    padrao = r'\b\d+(?:[.,]\d+)?\b'
    numeros = re.findall(padrao, frase_limpa)
    if numeros:
        return numeros[0].replace(",", ".")
    return ""


def detetar_idioma_e_processar(frase):
    frase_lower = frase.lower()
    valor = extrair_valor_universal(frase)
    local = ""

    palavras_tempo = ["hoje", "ontem", "agora", "já", "hoy", "ayer", "today", "yesterday"]
    palavras = frase.split()

    if palavras:
        if palavras[-1].lower().strip(".,!€$") in palavras_tempo and len(palavras) > 1:
            local = palavras[-2].strip(".,!€$")
        else:
            local = palavras[-1].strip(".,!€$")

    for marca in ["mercadona", "pingo doce", "continente", "mcdonalds", "uber", "carrefour", "auchan", "lidl"]:
        if marca in frase_lower:
            local = marca.capitalize()
            break

    tipo = analisar_tipo_fluxo(frase_lower)
    categoria = "Outros Gastos"

    if any(k in frase_lower for k in ["renda", "aluguel", "aluguer", "luz", "agua", "água", "internet"]):
        categoria = "🏠 Contas Fixas"
        if not local or local.lower() in palavras_tempo:
            local = "Contas Fixas"

    if tipo == "Entrada":
        condicao_salario = ["salario", "salário", "ordenado", "recebi"]
        categoria = "💰 Ordenado/Ganhos" if any(k in frase_lower for k in condicao_salario) else "📈 Faturação/Extras"
    else:
        if category := "🏠 Contas Fixas":
            pass
        if categoria == "Outros Gastos":
            if any(k in frase_lower for k in ["mercadona", "continente", "pingo", "lidl", "carrefour", "auchan"]):
                categoria = "🛒 Supermercado/Casa"
            elif any(k in frase_lower for k in ["mcdonalds", "restaurante", "restaurant", "cafe", "café"]):
                categoria = "🍕 Lazer/Alimentação Fora"
            elif any(k in frase_lower for k in ["uber", "taxi", "galp", "bp", "gasolina"]):
                categoria = "🚗 Transporte/Combustível"

    return tipo, valor, local.strip().capitalize(), categoria


def transcrever_audio_whatsapp(url_audio):
    if not client:
        return ""
    
    arquivo_ogg = os.path.join(os.getcwd(), "audio_temp.ogg")
    arquivo_wav = os.path.join(os.getcwd(), "audio_temp.wav")

    try:
        # Injeção global rigorosa do conversor binário FFmpeg local
        import pydub
        diretorio_atual = os.getcwd()
        ffmpeg_local = os.path.join(diretorio_atual, "ffmpeg")
        if os.path.exists(ffmpeg_local):
            pydub.AudioSegment.converter = ffmpeg_local

        resposta = requests.get(url_audio)
        with open(arquivo_ogg, "wb") as f:
            f.write(resposta.content)

        # Conversão via pydub utilizando o binário injetado
        audio = pydub.AudioSegment.from_file(arquivo_ogg, format="ogg")
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export(arquivo_wav, format="wav")

        # Upload estável usando o barramento de arquivos do novo SDK
        audio_file_gemini = client.files.upload(file=arquivo_wav)

        resposta_gemini = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=[
                "Transcreva este arquivo de áudio exatamente como ele foi falado. "
                "Retorne única e exclusivamente o texto transcrito puro, sem introduções ou explicações adicionais.",
                audio_file_gemini
            ]
        )

        texto_transcrito = resposta_gemini.text.strip()
        
        try:
            client.files.delete(name=audio_file_gemini.name)
        except Exception:
            pass

        return texto_transcrito

    except Exception as e:
        print(f"❌ Erro crítico no motor de áudio: {e}")
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
            "Analise este recibo ou nota de compra. Extraia o valor total gasto e o nome do estabelecimento local. "
            "Formate a resposta estritamente em uma única linha no formato: 'Gastei VALOR no LOCAL'. "
            "Exemplo de saída: Gastei 24.50 no Continente."
        )

        resposta_gemini = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=[prompt, foto_gemini]
        )
        resultado = resposta_gemini.text.strip()

        try:
            client.files.delete(name=foto_gemini.name)
        except Exception:
            pass

        return resultado

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
        tipo, v, l, c = detetar_idioma_e_processar(texto_recebido)

        if v and l:
            data_atual = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(csv_usuario, "a", encoding="utf-8") as f:
                f.write(f"{data_atual},{tipo},{v},{l},{c}\n")

            if tipo == "Entrada":
                msg.body(f"💰 *Vio:* Identifiquei uma Entrada! Transcrito: *\"{texto_recebido}\"* -> Salvo em *({c})*.")
            else:
                msg.body(f"✅ *Vio:* Despesa registrada! Transcrito: *\"{texto_recebido}\"* -> *{l}* em *({c})*.")
        else:
            msg.body(f"⚠️ *Vio:* Processado: \"{texto_recebido}\", mas não encontrei o valor e local explicitamente.")
    else:
        if num_midias > 0:
            if "image" in tipo_midia:
                msg.body("⚠️ *Vio:* Não consegui ler esta foto. Certifica-te de que o recibo está legível.")
            else:
                msg.body("⚠️ *Vio:* Recebi o teu áudio, mas o interpretador falhou. Por favor, digita ou repete o áudio.")

    return str(resposta_twilio)


if __name__ == "__main__":
    app.run(port=5000)