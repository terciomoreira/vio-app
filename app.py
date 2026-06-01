import os
import re
import sys
import types
from datetime import datetime
import google.generativeai as genai
import requests
import spacy
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

# 1. ORDEM CRÍTICA: Emulação do módulo aifc antes de qualquer outro import
sys.modules['aifc'] = types.ModuleType('aifc')

# Inicializa o Flask
app = Flask(__name__)

# Configuração Global da API do Gemini (Lê o VALUE que já tens no Render!)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    print("🚀 API do Gemini vinculada com sucesso a partir do Render!")
else:
    print("⚠️ AVISO: GEMINI_API_KEY não detetada nas variáveis!")

# Garante o download e carregamento do modelo de IA em Português
try:
    try:
        nlp = spacy.load("pt_core_news_sm")
    except OSError:
        print("📥 Modelo pt_core_news_sm não encontrado. Baixando automático...")
        from spacy.cli import download
        download("pt_core_news_sm")
        nlp = spacy.load("pt_core_news_sm")
    print("🚀 IA do SpaCy carregada com sucesso!")
except Exception as e:
    print(f"❌ Erro ao carregar o SpaCy: {e}")

# Dicionário de contingência mantido para compatibilidade
nlp_modelos = {}


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
        "ganado", "ingreso", "sueldo", "recibido",
        "earned", "received", "income", "salary", "deposit",
        "reçu", "gagné", "salaire", "facture",
        "получил", "доход", "зарплата", "دخل", "استlemت", "راتب", "收到", "收入", "工资"
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

    palavras_tempo = ["hoje", "ontem", "agora",
                      "já", "hoy", "ayer", "today", "yesterday"]

    palavras = frase.split()
    if palavras:
        if palavras[-1].lower().strip(".,!€$") in palavras_tempo and len(palavras) > 1:
            local = palavras[-2].strip(".,!€$")
        else:
            local = palavras[-1].strip(".,!€$")

    for marca in ["mercadona", "pingo doce", "continente", "mcdonalds", "uber", "carrefour", "auchan", "lidl", "yandex"]:
        if marca in frase_lower:
            local = marca.capitalize()
            break

    tipo = analisar_tipo_fluxo(frase_lower)
    categoria = "Outros Gastos"

    if any(k in frase_lower for k in ["renda", "aluguel", "aluguer", "luz", "agua", "água", "internet", "alquiler", "rent", "loyer"]):
        categoria = "🏠 Contas Fixas"
        if not local or local.lower() in palavras_tempo or local == "":
            local = "Contas Fixas"

    if tipo == "Entrada":
        condicao_salario = ["salario", "salário", "ordenado",
                            "recebi", "sueldo", "salary", "salaire", "зарплата"]
        categoria = "💰 Ordenado/Ganhos" if any(
            k in frase_lower for k in condicao_salario) else "📈 Faturação/Extras"
    else:
        if categoria == "Outros Gastos":
            if any(k in frase_lower for k in ["mercadona", "continente", "pingo", "lidl", "carrefour", "supermarche", "grocery", "groceries", "продукты"]):
                categoria = "🛒 Supermercado/Casa"
            elif any(k in frase_lower for k in ["mcdonalds", "restaurante", "restaurant", "cafe", "café", "bar", "ресторан"]):
                categoria = "🍕 Lazer/Alimentação Fora"
            elif any(k in frase_lower for k in ["uber", "taxi", "galp", "bp", "gasolina", "combustivel", "fuel", "essence", "такси"]):
                categoria = "🚗 Transporte/Combustível"

    return tipo, valor, local.strip().capitalize(), categoria


def transcrever_audio_whatsapp(url_audio):
    arquivo_ogg = os.path.join(os.getcwd(), "audio_temp.ogg")
    arquivo_wav = os.path.join(os.getcwd(), "audio_temp.wav")

    try:
        diretorio_atual = os.getcwd()
        ffmpeg_local = os.path.join(diretorio_atual, "ffmpeg")
        ffprobe_local = os.path.join(diretorio_atual, "ffprobe")

        if os.path.exists(ffmpeg_local):
            import pydub
            pydub.AudioSegment.converter = ffmpeg_local
            pydub.AudioSegment.ffmpeg = ffmpeg_local
            pydub.AudioSegment.ffprobe = ffprobe_local

        resposta = requests.get(url_audio)
        with open(arquivo_ogg, "wb") as f:
            f.write(resposta.content)

        from pydub import AudioSegment
        audio = AudioSegment.from_file(arquivo_ogg, format="ogg")
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export(arquivo_wav, format="wav")

        # Upload para o Gemini usando a API Key global
        audio_file_gemini = genai.upload_file(
            path=arquivo_wav, mime_type="audio/wav")
        modelo = genai.GenerativeModel("gemini-1.5-flash")

        resposta_gemini = modelo.generate_content([
            "Transcreva este arquivo de áudio exatamente como ele foi falado. "
            "Não adicione nenhuma introdução, explicação ou comentário, apenas retorne o texto transcrito puro.",
            audio_file_gemini
        ])

        texto_transcrito = resposta_gemini.text.strip()

        try:
            genai.delete_file(audio_file_gemini.name)
        except Exception:
            pass

        return texto_transcrito

    except Exception as e:
        print(f"❌ Erro crítico no motor Gemini de áudio: {e}")
        return ""
    finally:
        if os.path.exists(arquivo_ogg):
            os.remove(arquivo_ogg)
        if os.path.exists(arquivo_wav):
            os.remove(arquivo_wav)


def escanear_recibo_gemini(url_imagem):
    """Lê imagens de faturas/recibos usando Visão Computacional do Gemini"""
    arquivo_img = os.path.join(os.getcwd(), "temp_recibo.jpg")
    try:
        resposta = requests.get(url_imagem)
        with open(arquivo_img, "wb") as f:
            f.write(resposta.content)

        foto_gemini = genai.upload_file(
            path=arquivo_img, mime_type="image/jpeg")
        modelo = genai.GenerativeModel("gemini-1.5-flash")

        prompt = (
            "Analise este recibo, cupom fiscal ou nota de compra. Extraia o valor total gasto e o nome do estabelecimento local. "
            "Formate a resposta estritamente em uma única linha no formato: 'Gastei VALOR no LOCAL'. "
            "Exemplo de saída: Gastei 24.50 no Continente. Não escreva mais nada além disso."
        )

        resposta_gemini = modelo.generate_content([prompt, foto_gemini])
        resultado = resposta_gemini.text.strip()
        print(f"🎯 Scanner Gemini concluiu leitura da nota: '{resultado}'")

        try:
            genai.delete_file(foto_gemini.name)
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
    # Deteta o formato real enviado pela Twilio
    tipo_midia = request.values.get("MediaContentType0", "")

    resposta_twilio = MessagingResponse()
    msg = resposta_twilio.message()

    # Fluxo de Mídia Interativo
    if num_midias > 0:
        url_midia = request.values.get("MediaUrl0", "")
        if url_midia:
            # Se for áudio
            if "audio" in tipo_midia or "ogg" in url_midia:
                print(f"📥 Áudio recebido! URL: {url_midia}")
                texto_recebido = transcrever_audio_whatsapp(url_midia)
            # Se for uma imagem/foto do recibo
            elif "image" in tipo_midia:
                print(f"📥 Foto de Recibo recebida! URL: {url_midia}")
                texto_recebido = escanear_recibo_gemini(url_midia)

    # Processamento e gravação dos dados no CSV
    if texto_recebido:
        tipo, v, l, c = detetar_idioma_e_processar(texto_recebido)

        if v and l:
            data_atual = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(csv_usuario, "a", encoding="utf-8") as f:
                f.write(f"{data_atual},{tipo},{v},{l},{c}\n")

            if tipo == "Entrada":
                msg.body(
                    f"💰 *Vio:* Identifiquei uma Entrada! Transcrito: *\"{texto_recebido}\"* -> Salvo em *({c})*.")
            else:
                msg.body(
                    f"✅ *Vio:* Despesa registrada! Transcrito: *\"{texto_recebido}\"* -> *{l}* em *({c})*.")
        else:
            msg.body(
                f"⚠️ *Vio:* Consegui processar a mensagem: \"{texto_recebido}\", mas não encontrei o valor ou o local claramente.")
    else:
        if num_midias > 0:
            if "image" in tipo_midia:
                msg.body(
                    "⚠️ *Vio:* Não consegui ler esta foto. Garante que o recibo está bem focado e com o valor visível.")
            else:
                msg.body(
                    "⚠️ *Vio:* Recebi o teu arquivo de voz, mas não consegui extrair o texto dele. Por favor, fala de forma mais clara ou pausada.")

    return str(resposta_twilio)


if __name__ == "__main__":
    app.run(port=5000)
