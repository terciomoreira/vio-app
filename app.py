import spacy
from datetime import datetime
from pydub import AudioSegment
import speech_recognition as sr
import requests
import re
import os
from twilio.twiml.messaging_response import MessagingResponse
from flask import Flask, request
import sys
import types
# Correção essencial para compatibilidade com Python 3.14 no Render (Simulação do módulo aifc)
sys.modules['aifc'] = types.ModuleType('aifc')


# Inicializa o Flask
app = Flask(__name__)

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
        "получил", "доход", "зарплата", "دخل", "استلمت", "راتb", "收到", "收入", "工资"
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

    palavras = frase.split()
    if palavras:
        local = palavras[-1].strip(".,!€$")

    for marca in ["mercadona", "pingo doce", "continente", "mcdonalds", "uber", "carrefour", "auchan", "lidl", "yandex"]:
        if marca in frase_lower:
            local = marca.capitalize()
            break

    tipo = analisar_tipo_fluxo(frase_lower)
    categoria = "Outros Gastos"

    if any(k in frase_lower for k in ["renda", "aluguel", "aluguer", "luz", "agua", "água", "internet", "alquiler", "rent", "loyer"]):
        categoria = "🏠 Contas Fixas"
        if not local or local.lower() in ["hoje", "hoy", "today", ""]:
            local = "Contas Fixas"

    if tipo == "Entrada":
        if categoria != "🏠 Contas Fixas":
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
    try:
        resposta = requests.get(url_audio)
        arquivo_ogg = "/tmp/audio_temp.ogg"
        arquivo_wav = "/tmp/audio_temp.wav"

        with open(arquivo_ogg, "wb") as f:
            f.write(resposta.content)

        audio = AudioSegment.from_ogg(arquivo_ogg)
        audio.export(arquivo_wav, format="wav")

        reconhecedor = sr.Recognizer()
        with sr.AudioFile(arquivo_wav) as fonte:
            dados_audio = reconhecedor.record(fonte)
            texto_transcrito = reconhecedor.recognize_google(
                dados_audio, language="pt-PT")

        if os.path.exists(arquivo_ogg):
            os.remove(arquivo_ogg)
        if os.path.exists(arquivo_wav):
            os.remove(arquivo_wav)

        return texto_transcrito
    except Exception as e:
        print(f"❌ Erro de processamento de áudio na nuvem: {e}")
        return ""


@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    remetente = request.values.get("From", "")
    csv_usuario = obter_arquivo_usuario(remetente)

    texto_recebido = request.values.get("Body", "").strip()
    num_midias = int(request.values.get("NumMedia", 0))

    resposta_twilio = MessagingResponse()
    msg = resposta_twilio.message()

    if num_midias > 0:
        tipo_midia = request.values.get("MediaContentType0", "")
        if "audio" in tipo_midia or "ogg" in tipo_midia:
            url_audio = request.values.get("MediaUrl0", "")
            texto_recebido = transcrever_audio_whatsapp(url_audio)
            print(f"🎙️ Áudio Transcrito: '{texto_recebido}'")

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
                f"⚠️ *Vio:* Consegui ouvir: \"{texto_recebido}\", mas não encontrei o valor ou o local claramente.")
    else:
        if num_midias > 0:
            msg.body(
                "⚠️ *Vio:* Não consegui processar o arquivo de voz. Por favor, tente falar de forma pausada.")

    return str(resposta_twilio)


if __name__ == "__main__":
    app.run(port=5000)
