from google.genai import types
from google import genai
import psycopg2  # Conector do PostgreSQL
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from flask import Flask, request, send_file, render_template_string
import requests
from datetime import datetime
import re
import csv
import json
import os
-- Active: 1781974227496@@dpg-d8g3gkj7uimc73ft455g-a.oregon-postgres.render.com@5432

# NOVA IMPORTAÇÃO OFICIAL DO GEMINI

app = Flask(__name__, static_folder='static', static_url_path='/static')

# Configurações Globais via Variáveis de Ambiente na Render
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER")
DATABASE_URL = os.environ.get("DATABASE_URL")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

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
    id_limpo = str(id_whatsapp).replace("whatsapp:", "").strip()
    conn = obter_conexao_banco()
    if not conn:
        return False
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO usuarios (id_whatsapp, plano_ativo, data_validade) 
                       VALUES (%s, TRUE, CURRENT_TIMESTAMP + INTERVAL '7 days') 
                       ON CONFLICT (id_whatsapp) DO NOTHING;""",
                    (id_limpo,)
                )
        return True
    except Exception as e:
        print(f"❌ Erro ao registar utilizador no banco: {e}")
        return False
    finally:
        if conn:
            conn.close()


def verificar_assinatura_ativa(id_whatsapp):
    id_limpo = str(id_whatsapp).replace("whatsapp:", "").strip()
    conn = obter_conexao_banco()
    if not conn:
        return True
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT plano_ativo, data_validade FROM usuarios WHERE id_whatsapp = %s;", (id_limpo,))
            resultado = cursor.fetchone()

            if resultado:
                ativo, validade = resultado
                if not ativo or (validade and datetime.now() > validade):
                    return False
            return True
    except Exception as e:
        print(f"⚠️ Erro ao verificar assinatura: {e}")
        return True
    finally:
        if conn:
            conn.close()


def salvar_transacao_banco(id_whatsapp, tipo, valor, local, category, texto_puro, lista_itens=None):
    id_limpo = str(id_whatsapp).replace("whatsapp:", "").strip()
    verificar_e_registrar_usuario(id_limpo)

    try:
        if isinstance(valor, str):
            valor = re.sub(r"[^\d.,]", "", valor)
            if "," in valor and "." in valor:
                valor = valor.replace(",", "")
            elif "," in valor:
                valor = valor.replace(",", ".")
        valor_numerico = float(valor) if valor else 0.0
    except (ValueError, TypeError):
        print(
            f"⚠️ Aviso: Valor '{valor}' inválido recebido. Convertido para 0.0.")
        valor_numerico = 0.0

    conn = obter_conexao_banco()
    if not conn:
        return False
    try:
        with conn:
            with conn.cursor() as cursor:
                # Inserção principal retornando o ID gerado
                cursor.execute(
                    """INSERT INTO transacoes (id_whatsapp, tipo, valor, local, categoria, texto_puro)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING id;""",
                    (id_limpo, tipo, valor_numerico, local, category, texto_puro)
                )
                transacao_id = cursor.fetchone()[0]

                # Se houver itens detalhados extraídos pela IA, salva na tabela filha
                if lista_itens and isinstance(lista_itens, list):
                    for item in lista_itens:
                        try:
                            orig = item.get("original", "Item desconhecido")
                            trad = item.get("traduzido", orig)
                            qtd = int(item.get("qtd", 1))
                            p_un = float(item.get("preco_un", 0.0))
                            p_tot = float(item.get("total", p_un * qtd))

                            cursor.execute(
                                """INSERT INTO itens_transacao (transacao_id, nome_original, nome_traduzido, quantidade, preco_unitario, preco_total)
                                   VALUES (%s, %s, %s, %s, %s, %s);""",
                                (transacao_id, orig, trad, qtd, p_un, p_tot)
                            )
                        except Exception as e_item:
                            print(
                                f"⚠️ Erro ao inserir item individual da compra: {e_item}")

        print("💾 Transação completa e itens gravados com sucesso no PostgreSQL!")
        return True
    except Exception as e:
        print(f"❌ Erro ao inserir transação no PostgreSQL: {e}")
        return False
    finally:
        if conn:
            conn.close()


def apagar_ultima_transacao(id_whatsapp):
    """Procura e elimina a última transação inserida pelo usuário"""
    conexao = obter_conexao_banco()  # use a sua função existente de conexão
    if not conexao:
        return False

    try:
        with conexao.cursor() as cur:
            # Seleciona o ID da última transação desse usuário usando o ID sequencial ou data
            cur.execute(
                "SELECT id FROM transacoes WHERE id_whatsapp = %s ORDER BY id DESC LIMIT 1;",
                (id_whatsapp,)
            )
            resultado = cur.fetchone()

            if resultado:
                id_transacao = resultado[0]
                # Elimina a transação específica
                cur.execute("DELETE FROM transacoes WHERE id = %s;",
                            (id_transacao,))
                conexao.commit()
                return True
            return False
    except Exception as e:
        print(f"❌ Erro ao apagar última transação: {e}")
        return False
    finally:
        conexao.close()

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
    if not texto:
        return False

    palavra_limpa = texto.strip().lower()
    if palavra_limpa in ["resumo", "relatorio", "relatório", "contador", "summary"]:
        return True

    if not ai_client:
        return False

    prompt = (
        "Atue como um classificador de intenção linguística de alta precisão de nível global.\n"
        "Analise a mensagem enviada pelo usuário in qualquer idioma e determine se ele está solicitando um resumo, relatório, extrato ou balanço de finanças.\n"
        "Se for um lançamento de despesa comum ou uma notificação bancária recebida, responda obrigatoriamente false.\n\n"
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
        return False


def inteligencia_universal_gemini(texto_ou_transcricao):
    if not ai_client or not texto_ou_transcricao:
        return "Saída", "", "Desconhecido", "🛒 Outros Gastos", None, "pt"

    prompt = (
        "Atue como um analista financeiro multilíngue de altíssima precisão.\n"
        "Analise o texto fornecido pelo usuário (pode estar em português, espanhol, catalão, inglês, francês, russo, chinês, japonês, etc).\n"
        "Identifique e extraia os dados fundamentais da transação financeira.\n\n"
        "Regras cruciais:\n"
        "1. Identifique o tipo: 'Entrada' se for ganho/salário/reembolso, 'Saída' se for gasto/compra/pagamento.\n"
        "2. Identifique o valor numérico exato com ponto decimal.\n"
        "3. Limpe o nome do Estabelecimento comercial.\n"
        "4. Atribua uma categoria coerente acompanhada de um emoji adequado.\n"
        "5. Identifique o IDIOMA em que o usuário enviou a mensagem (ex: 'pt', 'es', 'en', 'fr', 'ru', 'zh', 'ja').\n\n"
        f"Texto para análise: \"{texto_ou_transcricao}\"\n\n"
        "Formatos JSON estrito exigido:\n"
        "{\n"
        '  "tipo": "Entrada" ou "Saída",\n'
        '  "valor": "9.55",\n'
        '  "local": "Nome limpo do estabelecimento",\n'
        '  "categoria": "Emoji + Categoria",\n'
        '  "idioma_usuario": "código do idioma detetado"\n'
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
            dados.get("categoria", "🛒 Outros Gastos"),
            dados.get("idioma_usuario", "pt")
        )
    except Exception as e:
        print(f"❌ Erro na análise de texto do Gemini: {e}")
        valores = re.findall(r"\d+(?:[.,]\d+)?", texto_ou_transcricao)
        v = valores[0].replace(",", ".") if valores else ""
        return "Saída", v, "Não especificado", "🛒 Outros Gastos", "pt"


def processar_midia_url(url_midia, mime_type):
    if not ai_client:
        return {}
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
            prompt = "Transcreva este áudio com atenção aos valores e locais mencionados. Preserve a linguagem natural."
            resposta_gemini = ai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[midia_gemini, prompt]
            )
            return {"texto_puro": resposta_gemini.text.strip()}
        else:
            prompt = (
                "Você é um leitor óptico (OCR) financeiro universal de última geração.\n"
                "Analise este talão de compra / recibo / fatura simplificada de qualquer lugar do mundo (Catalunha, Espanha, Rússia, Japão, etc).\n"
                "1. Extraia o valor TOTAL da compra e o nome limpo do estabelecimento.\n"
                "2. Extraia TODOS os produtos comprados da lista.\n"
                "3. IMPORTANTE: Identifique o idioma do talão e TRADUZA o nome de cada item automaticamente para o idioma do usuário. Se o talão estiver em Catalão/Espanhol (ex: Figueres), traduza os itens de forma precisa (ex: 'Bossa plastic' -> 'Saco Plástico', 'Cigró M.Cuit' -> 'Grão-de-bico Cozido'). Se o talão for de outro país, traduza para o idioma correspondente ou português por padrão.\n"
                "4. Identifique o idioma de resposta adequado baseado no contexto do usuário.\n\n"
                "Retorne estritamente um objeto JSON estruturado como o modelo abaixo:\n"
                "{\n"
                '  "local": "Nome limpo do estabelecimento",\n'
                '  "total": "Valor total numérico (ex: 5.45)",\n'
                '  "categoria": "Emoji + Nome da Categoria Coerente",\n'
                '  "idioma_usuario": "pt",\n'
                '  "itens": [\n'
                '     {"original": "NOME ORIGINAL", "traduzido": "NOME TRADUZIDO", "qtd": 1, "preco_un": 1.20, "total": 1.20}\n'
                '  ]\n'
                "}"
            )
            resposta_gemini = ai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[midia_gemini, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                ),
            )
            texto_json = resposta_gemini.text.strip().replace(
                "```json", "").replace("```", "").strip()
            print(
                f"📸 Resposta JSON Estruturada do Gemini para Imagem: {texto_json}")
            return json.loads(texto_json)

    except Exception as e:
        print(f"❌ Erro ao processar mídia: {e}")
        return {}
    finally:
        try:
            if 'midia_gemini' in locals():
                ai_client.files.delete(name=midia_gemini.name)
        except Exception:
            pass
        if os.path.exists(arquivo_temp):
            os.remove(arquivo_temp)


def traduzir_resposta_vios(mensagem_base, idioma_destino):
    """Garante que as mensagens do Vio cheguem no idioma nativo do utilizador"""
    if not ai_client or idioma_destino == "pt":
        return mensagem_base

    prompt = f"Traduza a seguinte mensagem do sistema financeiro para o idioma correspondente ao código '{idioma_destino}'. Mantenha a formatação Markdown e os Emojis intocados:\n\n{mensagem_base}"
    try:
        res = ai_client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt)
        return res.text.strip()
    except Exception:
        return mensagem_base


# ==========================================
#  ROTA: GERADOR E DOWNLOAD DE EXCEL/CSV
# ==========================================

@app.route("/download/<id_usuario>", methods=["GET"])
def download_relatorio(id_usuario):
    csv_path = f"/tmp/extrato_{id_usuario}.csv"
    conn = obter_conexao_banco()
    if conn:
        try:
            id_com_mais = "+" + \
                id_usuario if not id_usuario.startswith("+") else id_usuario
            id_sem_mais = id_usuario.replace("+", "")

            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT data_transacao, tipo, valor, local, categoria 
                    FROM transacoes 
                    WHERE id_whatsapp = %s OR id_whatsapp = %s
                    ORDER BY data_transacao DESC;
                """, (id_com_mais, id_sem_mais))
                linhas = cursor.fetchall()

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
        finally:
            conn.close()
    return "Banco offline", 500


# ==========================================
#  ROTA: LANDING PAGE OFICIAL DO VIO
# ==========================================
@app.route("/")
def index():
    html_landing_page = """
    <!DOCTYPE html>
    <html lang="pt">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Vio - O Seu Gestor Financeiro por Voz</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#040814] text-white font-sans antialiased selection:bg-blue-500 selection:text-white">
        <header class="max-w-6xl mx-auto px-6 py-6 flex justify-between items-center border-b border-gray-900">
            <div class="flex items-center space-x-3">
                <span class="text-2xl font-black tracking-wider bg-gradient-to-r from-blue-400 to-indigo-500 bg-clip-text text-transparent">VIO</span>
            </div>
            <a href="#precos" class="bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-medium px-6 py-2.5 rounded-full transition duration-300 text-sm shadow-md shadow-blue-600/10">
                Aceder ao WhatsApp
            </a>
        </header>

        <section class="max-w-5xl mx-auto px-6 pt-16 pb-12 text-center">
            <div class="flex justify-center mb-8">
                <img src="https://github.com/terciomoreira/vio-app/blob/main/static/logo-vio.jpeg.jpeg?raw=true" alt="Logo Vio" class="w-40 h-auto rounded-2xl shadow-xl shadow-blue-500/5 border border-gray-800">
            </div>
            
            <h1 class="text-4xl md:text-6xl font-black tracking-tight leading-none bg-gradient-to-r from-white via-blue-100 to-blue-400 bg-clip-text text-transparent">
                O Seu Gestor Financeiro<br class="hidden md:block"> por Voz
            </h1>
            <p class="mt-6 text-lg md:text-xl text-gray-400 max-w-2xl mx-auto font-normal leading-relaxed">
                Controla todas as tuas despesas e ganhos enviando apenas mensagens de áudio ou texto no WhatsApp. Inteligência artificial pura, sem complicações.
            </p>
            <div class="mt-10">
                <a href="#precos" class="inline-block bg-blue-600 hover:bg-blue-500 text-white font-bold px-8 py-4 rounded-xl text-lg transition duration-200 shadow-xl shadow-blue-600/20 transform hover:-translate-y-0.5">
                    Experimentar Grátis por 7 Dias
                </a>
            </div>
        </section>

        <!-- SECÇÃO ATUALIZADA: INSTRUÇÕES DETALHADAS DE USO -->
        <section class="max-w-4xl mx-auto px-6 py-12">
            <div class="text-center mb-10">
                <h2 class="text-2xl md:text-3xl font-extrabold tracking-tight bg-gradient-to-r from-white to-gray-400 bg-clip-text text-transparent">
                    Como funciona? É incrivelmente simples
                </h2>
                <p class="text-gray-400 mt-2 text-sm md:text-base">Esquece planilhas chatas e apps complexos. Fala com o Vio como falas com um amigo.</p>
            </div>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div class="bg-[#080d1f] border border-gray-800 rounded-2xl p-6 hover:border-blue-500/30 transition duration-300">
                    <div class="flex items-center space-x-3 mb-3">
                        <span class="text-2xl">🎙️</span>
                        <h4 class="font-bold text-white tracking-wide">Por Voz ou Texto</h4>
                    </div>
                    <p class="text-gray-400 text-sm leading-relaxed">
                        Envia um áudio natural ou mensagem de texto no WhatsApp dizendo o que ganhaste ou gastaste. <br>
                        <span class="text-blue-400 font-medium italic mt-1 block">Ex: "Recebi 1500€ de salário hoje" ou "Paguei 12.50€ no almoço".</span>
                    </p>
                </div>

                <div class="bg-[#080d1f] border border-gray-800 rounded-2xl p-6 hover:border-blue-500/30 transition duration-300">
                    <div class="flex items-center space-x-3 mb-3">
                        <span class="text-2xl">📸</span>
                        <h4 class="font-bold text-white tracking-wide">Fotos de Faturas e Recibos</h4>
                    </div>
                    <p class="text-gray-400 text-sm leading-relaxed">
                        Tira uma foto de qualquer talão de compra. O Vio faz a leitura inteligente completa (OCR), extrai o total, categoriza e lista detalhadamente os produtos no banco.
                    </p>
                </div>

                <div class="bg-[#080d1f] border border-gray-800 rounded-2xl p-6 hover:border-blue-500/30 transition duration-300">
                    <div class="flex items-center space-x-3 mb-3">
                        <span class="text-2xl">📊</span>
                        <h4 class="font-bold text-white tracking-wide">Comandos de Resumo Instantâneo</h4>
                    </div>
                    <p class="text-gray-400 text-sm leading-relaxed">
                        Escreve ou diz a palavra <span class="text-indigo-400 font-semibold">"Resumo"</span> para receber um balanço. Podes refinar por tempo adicionando <span class="text-indigo-400 italic">"Semana", "Trimestre" ou "Ano"</span>.
                    </p>
                </div>

                <div class="bg-[#080d1f] border border-gray-800 rounded-2xl p-6 hover:border-blue-500/30 transition duration-300">
                    <div class="flex items-center space-x-3 mb-3">
                        <span class="text-2xl">🌍</span>
                        <h4 class="font-bold text-white tracking-wide">Suporte Multilíngue Inteligente</h4>
                    </div>
                    <p class="text-gray-400 text-sm leading-relaxed">
                        O Vio deteta o idioma da mensagem ou do talão automaticamente (seja Inglês, Espanhol, Catalão ou Russo), processa e responde-te sempre na tua língua nativa.
                    </p>
                </div>
            </div>
        </section>

        <section class="max-w-4xl mx-auto px-6 py-4">
            <div class="bg-[#090f24] rounded-2xl border border-gray-800/60 p-8 shadow-2xl relative overflow-hidden group">
                <div class="absolute top-0 left-0 w-full h-[2px] bg-gradient-to-r from-transparent via-blue-500 to-transparent"></div>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-8 text-center relative z-10">
                    <div class="flex flex-col items-center p-4">
                        <div class="w-12 h-12 bg-blue-500/10 rounded-full flex items-center justify-center text-blue-400 mb-4 text-xl">🎙️</div>
                        <h3 class="font-bold text-lg text-white tracking-wide">REGISTO DIRETO</h3>
                        <p class="text-sm text-gray-400 mt-2 italic">"Gastei 50 euros no Pingo Doce"</p>
                    </div>
                    <div class="flex flex-col items-center p-4 border-y md:border-y-0 md:border-x border-gray-800/80">
                        <div class="w-12 h-12 bg-indigo-500/10 rounded-full flex items-center justify-center text-indigo-400 mb-4 text-xl">📋</div>
                        <h3 class="font-bold text-lg text-white tracking-wide">CATEGORIZAÇÃO</h3>
                        <p class="text-sm text-gray-400 mt-2">Separação automática para o e-fatura</p>
                    </div>
                    <div class="flex flex-col items-center p-4">
                        <div class="w-12 h-12 bg-cyan-500/10 rounded-full flex items-center justify-center text-cyan-400 mb-4 text-xl">📊</div>
                        <h3 class="font-bold text-lg text-white tracking-wide">EXPORTAÇÃO</h3>
                        <p class="text-sm text-gray-400 mt-2">Relatórios prontos para o seu IRS / Contador</p>
                    </div>
                </div>
            </div>
        </section>

        <section id="precos" class="max-w-4xl mx-auto px-6 py-20 text-center">
            <div class="max-w-md mx-auto bg-[#090f24] border border-blue-500/20 rounded-3xl p-8 shadow-2xl relative">
                <span class="absolute -top-3 left-1/2 transform -translate-x-1/2 bg-blue-600 text-xs uppercase font-extrabold px-3 py-1 rounded-full tracking-wider">Acesso Total</span>
                <h3 class="text-xl font-bold text-gray-200 mt-2">Assinatura Mensal Vio</h3>
                <div class="mt-6 flex justify-center items-baseline text-white">
                 <span class="text-5xl font-black tracking-tight">5,90€</span>
                 <span class="ml-2 text-lg text-gray-400 font-medium">/mês + IVA</span>
              </div>
                <ul class="mt-8 space-y-4 text-sm text-gray-300 text-left border-t border-gray-800/60 pt-6">
                    <li class="flex items-center space-x-3">
                        <span class="text-blue-400">✔</span> <span>Lançamentos por áudio e texto ilimitados</span>
                    </li>
                    <li class="flex items-center space-x-3">
                        <span class="text-blue-400">✔</span> <span>Inteligência Artificial Ativa </span>
                    </li>
                    <li class="flex items-center space-x-3">
                        <span class="text-blue-400">✔</span> <span>Comando "resumo" com análise imediata</span>
                    </li>
                    <li class="flex items-center space-x-3">
                        <span class="text-blue-400">✔</span> <span>Download direto do Excel consolidado</span>
                    </li>
                </ul>
                <div class="mt-8">
                    <a href="https://buy.stripe.com/5kQ4gz2F2cA34S1dkm1Nu00" class="block w-full bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-bold py-3.5 px-4 rounded-xl transition duration-200 text-center shadow-lg shadow-indigo-600/10">
                        Ativar Conta com Stripe 💳
                    </a>
                </div>
                <p class="text-xs text-gray-500 mt-4">Cancelamento fácil a qualquer momento. Processado via Stripe.</p>
            </div>
        </section>

        <footer class="py-8 text-center text-xs text-gray-600 border-t border-gray-900 max-w-6xl mx-auto px-6">
            © 2026 Vio. Operando em conformidade com as diretrizes do ecossistema Twilio e Google Gemini AI Studio.
        </footer>
    </body>
    </html>
    """
    return render_template_string(html_landing_page)


@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    try:
        dados_evento = json.loads(payload)
        tipo_evento = dados_evento.get("type")

        print(f"💳 Webhook Stripe Recebido: {tipo_evento}")

        if tipo_evento == "checkout.session.completed":
            sessao = dados_evento.get("data", {}).get("object", {})
            id_whatsapp = sessao.get("metadata", {}).get("id_whatsapp")

            if id_whatsapp:
                id_limpo = str(id_whatsapp).replace(
                    "whatsapp:", "").replace("+", "").strip()
                conn = obter_conexao_banco()
                if conn:
                    try:
                        with conn:
                            with conn.cursor() as cursor:
                                cursor.execute("""
                                    UPDATE usuarios 
                                    SET plano_ativo = TRUE, data_validade = CURRENT_TIMESTAMP + INTERVAL '30 days'
                                    WHERE id_whatsapp = %s;
                                """, (id_limpo,))
                        print(
                            f"🚀 Usuário {id_limpo} ativado via Stripe com sucesso!")
                    except Exception as err:
                        print(
                            f"❌ Erro ao atualizar plano no banco via webhook: {err}")
                    finally:
                        conn.close()

        return json.dumps({"success": True}), 200
    except Exception as e:
        print(f"❌ Falha crítica no processamento do webhook: {e}")
        return "Erro Interno", 400


# ==========================================
#  WEBHOOK DO TWILIO (MECANISMO CENTRAL VIO)
# ==========================================

@app.route("/whatsapp", methods=["POST"])
def twilio_webhook():
    remetente = request.form.get("From", "")
    texto_recebido = request.form.get("Body", "")
    url_midia = request.form.get("MediaUrl0", "")
    mime_type = request.form.get("MediaContentType0", "")

    if texto_recebido:
        texto_recebido = str(texto_recebido).strip()

    id_usuario = remetente.replace("whatsapp:", "").replace("+", "").strip()
    verificar_e_registrar_usuario(id_usuario)
    # Intersecção do comando desfazer
    if texto_recebido:
        mensagem_limpa = texto_recebido.lower()
        if mensagem_limpa in ["desfazer", "apagar ultimo", "apagar último", "cancelar"]:
            sucesso = apagar_ultima_transacao(id_usuario)
            twiml_resp = MessagingResponse()

            if sucesso:
                twiml_resp.message(
                    "🗑️ *Vio:* Feito! A tua última transação foi apagada com sucesso e já não conta para o teu resumo.")
            else:
                twiml_resp.message(
                    "⚠️ *Vio:* Não encontrei nenhuma transação recente para apagar.")

            return str(twiml_resp)
        # Final da intersecção do comando desfazer

    if not verificar_assinatura_ativa(id_usuario):
        twiml_resp = MessagingResponse()
        twiml_resp.message(
            "🚫 *Vio:* O teu período de teste terminou ou a tua assinatura expirou.\n\n"
            "Para continuares a gerir as tuas finanças com inteligência artificial por apenas *5,90€/mês* + IVA, "
            "renova a tua conta aqui: https://vio.creariscoretech.com"
        )
        return str(twiml_resp)
    idioma_contexto = "pt"
    lista_itens_extraidos = None

    # Se receber Imagem ou Áudio
    if url_midia:
        resultado_midia = processar_midia_url(url_midia, mime_type)
        if isinstance(resultado_midia, dict):
            # Se for imagem processada pelo novo leitor estruturado JSON
            if "total" in resultado_midia:
                v_total = resultado_midia.get("total")
                v_local = resultado_midia.get("local", "Desconhecido")
                v_cat = resultado_midia.get("categoria", "🛒 Outros Gastos")
                idioma_contexto = resultado_midia.get("idioma_usuario", "pt")
                lista_itens_extraidos = resultado_midia.get("itens", [])

                # Monta a frase virtual para salvar o texto_puro coerente
                texto_recebido = f"Gastei {v_total} no {v_local}"
            else:
                texto_recebido = resultado_midia.get("texto_puro", "")
        else:
            texto_recebido = str(resultado_midia)

    # 1. LOGICA DE COMANDO DE RESUMO (SISTEMA DE SUPERPODERES ACIONADO)
    if texto_recebido and verificar_se_e_comando_resumo(texto_recebido):
        mensagem_min = texto_recebido.lower()

        # Filtros de tempo padrão e dinâmicos para PostgreSQL
        query_filtro = "data_transacao >= DATE_TRUNC('month', NOW())"
        periodo_texto = "deste mês"

        if "semana" in mensagem_min:
            query_filtro = "data_transacao >= NOW() - INTERVAL '7 days'"
            periodo_texto = "dos últimos 7 dias"
        elif "trimestre" in mensagem_min:
            query_filtro = "data_transacao >= NOW() - INTERVAL '3 months'"
            periodo_texto = "dos últimos 3 meses"
        elif "ano" in mensagem_min or "anual" in mensagem_min:
            query_filtro = "data_transacao >= NOW() - INTERVAL '1 year'"
            periodo_texto = "deste último ano"
        elif "5 anos" in mensagem_min:
            query_filtro = "data_transacao >= NOW() - INTERVAL '5 years'"
            periodo_texto = "dos últimos 5 anos"

        conn = obter_conexao_banco()
        if conn:
            try:
                id_com_mais = "+" + \
                    id_usuario if not id_usuario.startswith(
                        "+") else id_usuario
                id_sem_mais = id_usuario.replace("+", "")

                with conn.cursor() as cursor:
                    # Executa a query injetando dinamicamente o intervalo temporal correto
                    cursor.execute(f"""
                        SELECT categoria, SUM(valor) 
                        FROM transacoes 
                        WHERE tipo = 'Saída' AND (id_whatsapp = %s OR id_whatsapp = %s) AND {query_filtro}
                        GROUP BY categoria 
                        ORDER BY SUM(valor) DESC;
                    """, (id_com_mais, id_sem_mais))
                    linhas = cursor.fetchall()

                twiml_resp = MessagingResponse()
                msg = twiml_resp.message()

                if linhas:
                    resposta_texto = f"📊 *Vio: Aqui está o teu Resumo Financeiro {periodo_texto}!*\n\n"
                    total_geral = 0
                    for cat, val in linhas:
                        categoria_nome = cat if cat else "🛒 Outros Gastos"
                        resposta_texto += f"• {categoria_nome}: *{val:.2f} €*\n"
                        total_geral += val
                    resposta_texto += f"\n💰 *Total acumulado de Saídas:* *{total_geral:.2f} €*"

                    host_app = request.host_url.rstrip('/')
                    link_download = f"{host_app}/download/{id_usuario}"
                    resposta_texto += f"\n\n📥 *Descarrega o Excel consolidado:* {link_download}"

                    resposta_final = traduzir_resposta_vios(
                        resposta_texto, idioma_contexto)
                    msg.body(resposta_final)
                else:
                    resposta_texto = f"📊 *Vio:* Não encontrei nenhuma despesa registada {periodo_texto}."
                    msg.body(traduzir_resposta_vios(
                        resposta_texto, idioma_contexto))

                return str(twiml_resp)

            except Exception as e_banco:
                resposta_final = f"⚠️ *Vio:* Erro ao processar o teu resumo no banco: {e_banco}"
                twiml_resp = MessagingResponse()
                twiml_resp.message(resposta_final)
                return str(twiml_resp)
            finally:
                conn.close()
        else:
            twiml_resp = MessagingResponse()
            twiml_resp.message(
                "⚠️ *Vio:* O banco de dados está temporariamente inacessível.")
            return str(twiml_resp)

    # 2. LOGICA DE LANÇAMENTO COMUM (SEJA TEXTO DIRETO OU TRATADO VIA OCR)
    resposta_texto = ""
    if texto_recebido:
        # Se os dados já não vieram pré-estruturados do OCR de imagem, corre a inteligência de texto pura
        if lista_itens_extraidos is None:
            try:
                tipo, v, l, c, idioma_contexto = inteligencia_universal_gemini(
                    texto_recebido)
            except Exception as e_gemini:
                print(f"⚠️ Falha na IA: {e_gemini}")
                valores = re.findall(r"\d+(?:[.,]\d+)?", texto_recebido)
                v = valores[0].replace(",", ".") if valores else ""
                tipo, l, c = "Saída", "Não especificado", "🛒 Outros Gastos"
        else:
            # Caso os dados venham do OCR estruturado acima
            valores_orig = re.findall(r"\d+(?:[.,]\d+)?", texto_recebido)
            v = valores_orig[0].replace(",", ".") if valores_orig else "0.0"
            tipo, l, c = "Saída", v_total, v_cat

        if v:
            if not l or l.lower() == "desconhecido":
                l = "Não especificado"

            # Salva na base de dados (passando a lista de itens traduzidos se houver)
            gravou_no_banco = salvar_transacao_banco(
                id_whatsapp=id_usuario, tipo=tipo, valor=v, local=l, category=c, texto_puro=texto_recebido, lista_itens=lista_itens_extraidos
            )

            if not gravou_no_banco:
                try:
                    csv_usuario = obter_arquivo_usuario(id_usuario)
                    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M")
                    with open(csv_usuario, "a", encoding="utf-8") as f:
                        f.write(f"{data_atual},{tipo},{v},{l},{c}\n")
                except Exception as e_file:
                    print(f"❌ Erro crítico no Fallback CSV: {e_file}")

            # Geração do feedback final respeitando o idioma correspondente
            if tipo == "Entrada":
                resposta_texto = f"💰 *Vio:* Entendi: *\"{texto_recebido}\"* -> Entrada de {v} em *({c})*."
            else:
                resposta_texto = f"✅ *Vio:* Entendi: *\"{texto_recebido}\"* -> Despesa de {v} no {l} em *({c})*."
                if lista_itens_extraidos:
                    resposta_texto += f"\n\n📦 *Produtos Detetados ({len(lista_itens_extraidos)}):*"
                    # Exibe os primeiros 6 itens para não sobrecarregar o WhatsApp
                    for it in lista_itens_extraidos[:6]:
                        resposta_texto += f"\n• {it.get('traduzido')} ({it.get('qtd')}x) -> {it.get('total')} €"
                    if len(lista_itens_extraidos) > 6:
                        resposta_texto += f"\n_...e mais {len(lista_itens_extraidos) - 6} itens guardados no banco._"
        else:
            resposta_texto = f"⚠️ *Vio:* Entendi \"{texto_recebido}\", mas não consegui extrair os valores com precisão."
    else:
        resposta_texto = "⚠️ *Vio:* Recebi a tua mensagem, mas não consegui extrair nenhum conteúdo legível."

    # Devolve a resposta traduzida para o idioma em uso
    resposta_final_traduzida = traduzir_resposta_vios(
        resposta_texto, idioma_contexto)
    twiml_resp = MessagingResponse()
    twiml_resp.message(resposta_final_traduzida)
    return str(twiml_resp)


@app.route("/ativar-admin")
def ativar_admin():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Atualiza a assinatura e o trial do teu número
        cursor.execute("""
            UPDATE usuarios 
            SET status_assinatura = 'active',
                data_fim_teste = NOW() + INTERVAL '365 days'
            WHERE id_whatsapp LIKE '%351931477038%';
        """)

        conn.commit()
        cursor.close()
        conn.close()
        return "<h1>✅ Conta ativada com sucesso por 1 ano!</h1><p>Já podes enviar fotos no WhatsApp.</p>"
    except Exception as e:
        return f"<h1>Erro ao ativar:</h1><p>{str(e)}</p>"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
