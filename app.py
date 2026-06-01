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
# 1. ORDEM CRÍTICA: Emulação do módulo aifc antes de qualquer outro import
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
    # RECUPERADO: Lista internacional de ganhos/entradas
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
    valor = extrair_universal = extrair_valor_universal(frase)
    local = ""

    # RECUPERADO: Palavras de tempo multi-idioma
    palavras_tempo = ["hoje", "ontem", "agora",
                      "já", "hoy", "ayer", "today", "yesterday"]

    palavras = frase.split()
    if palavras:
        if palavras[-1].lower().strip(".,!€$") in palavras_tempo and len(palavras) > 1:
            local = palavras[-2].strip(".,!€$")
        else:
            local = palavras[-1].strip(".,!€$")

    # RECUPERADO: Marcas e redes internacionais
    for marca in ["mercadona", "pingo doce", "continente", "mcdonalds", "uber", "carrefour", "auchan", "lidl", "yandex"]:
        if marca in frase_lower:
            local = marca.capitalize()
            break

    tipo = analisar_tipo_fluxo(frase_lower)
    categoria = "Outros Gastos"

    # RECUPERADO: Categorização internacional de contas fixas
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
            # RECUPERADO: Supermercados globais
            if any(k in frase_lower for k in ["mercadona", "continente", "pingo", "lidl", "carrefour", "supermarche", "grocery", "groceries", "продукты"]):
                categoria = "🛒 Supermercado/Casa"
            # RECUPERADO: Alimentação global
            elif any(k in frase_lower for k in ["mcdonalds", "restaurante", "restaurant", "cafe", "café", "bar", "ресторан"]):
                categoria = "🍕 Lazer/Alimentação Fora"
            # RECUPERADO: Transportes globais
            elif any(k in frase_lower for k in ["uber", "taxi", "galp", "bp", "gasolina", "combustivel", "fuel", "essence", "такси"]):
                categoria = "🚗 Transporte/Combustível"

    return tipo, valor, local.strip().capitalize(), categoria


def transcrever_audio_whatsapp(url_audio):
    arquivo_ogg = "audio_temp.ogg"
    arquivo_wav = "audio_temp.wav"
    try:
        # CORREÇÃO LOCAL: Aponta para os binários baixados pelo build.sh na raiz do projeto
        diretorio_atual = os.path.dirname(os.path.abspath(__file__))
        ffmpeg_local = os.path.join(diretorio_atual, "ffmpeg")
        ffprobe_local = os.path.join(diretorio_atual, "ffprobe")

        # Se os binários locais existirem, injeta-os diretamente na Pydub
        if os.path.exists(ffmpeg_local):
            AudioSegment.converter = ffmpeg_local
            AudioSegment.ffmpeg = ffmpeg_local
            print(f"✅ FFmpeg local detetado em: {ffmpeg_local}")
        if os.path.exists(ffprobe_local):
            AudioSegment.ffprobe = ffprobe_local

        print(f"📥 Baixando áudio da Twilio...")
        resposta = requests.get(url_audio)
        with open(arquivo_ogg, "wb") as f:
            f.write(resposta.content)

        print("🔄 Convertendo OGG para WAV localmente...")
        audio = AudioSegment.from_file(arquivo_ogg, format="ogg")
        audio.export(arquivo_wav, format="wav")

        print("🎙️ Iniciando reconhecimento de voz multi-idioma...")
        reconhecedor = sr.Recognizer()

        # Ajuste de ruído dinâmico para melhorar a leitura de sotaques em áudios de rua/WhatsApp
        reconhecedor.dynamic_energy_threshold = True

        with sr.AudioFile(arquivo_wav) as fonte:
            # Ajusta o microfone virtual para o ruído de fundo do áudio recebido
            reconhecedor.adjust_for_ambient_noise(fonte, duration=0.5)
            dados_audio = reconhecedor.record(fonte)

        # Lista de idiomas/sotaques suportados em ordem de probabilidade do teu público
        # pt-PT (Portugal), pt-BR (Brasil), es-ES (Espanha), en-US (Inglês), fr-FR (França)
        idiomas_suportados = ["pt-PT", "pt-BR", "es-ES", "en-US", "fr-FR"]
        texto_transcrito = ""

        # Tentativa em cascata: testa os idiomas até conseguir uma tradução válida
        for idioma in idiomas_suportados:
            try:
                print(f"🗣️ Tentando decifrar sotaque/idioma em: {idioma}...")
                texto_transcrito = reconhecedor.recognize_google(
                    dados_audio, language=idioma)
                if texto_transcrito.strip():
                    print(
                        f"🎯 Sucesso com o idioma [{idioma}]: '{texto_transcrito}'")
                    break
            except sr.UnknownValueError:
                # O Google não entendeu neste idioma específico, pula para o próximo da lista
                continue
            except sr.RequestError:
                # Falha de conexão com a API do Google, tenta o próximo por segurança
                continue

        return texto_transcrito

    except Exception as e:
        print(f"❌ Erro no processamento de áudio: {e}")
        return ""

    finally:
        if os.path.exists(arquivo_ogg):
            os.remove(arquivo_ogg)
        if os.path.exists(arquivo_wav):
            os.remove(arquivo_wav)


@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    remetente = request.values.get("From", "")
    csv_usuario = obter_arquivo_usuario(remetente)

    texto_recebido = request.values.get("Body", "").strip()
    num_midias = int(request.values.get("NumMedia", 0))

    resposta_twilio = MessagingResponse()
    msg = resposta_twilio.message()

    if num_midias > 0:
        url_audio = request.values.get("MediaUrl0", "")
        if url_audio:
            print(f"📥 Áudio recebido da Twilio! URL: {url_audio}")
            texto_recebido = transcrever_audio_whatsapp(url_audio)
            print(f"🎙️ Resultado final da transcrição: '{texto_recebido}'")

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
            msg.body("⚠️ *Vio:* Recebi o teu arquivo de voz, mas não consegui extrair o texto dele. Por favor, fala de forma mais clara ou pausada.")

    return str(resposta_twilio)


if __name__ == "__main__":
    app.run(port=5000)
