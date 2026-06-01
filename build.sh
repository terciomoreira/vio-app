#!/usr/bin/env bash
# Sair imediatamente se algum comando falhar
set -o errexit

# 1. Instalar as dependências do Python normalmente
pip install -r requirements.txt

# 2. Baixar o FFmpeg binário pré-compilado para Linux de 64 bits
echo "📥 Baixando FFmpeg estático..."
curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o ffmpeg.tar.xz

# 3. Descompactar o arquivo
tar -xf ffmpeg.tar.xz

# 4. Mover os binários do ffmpeg e ffprobe diretamente para a raiz do projeto
mv ffmpeg-*-amd64-static/ffmpeg .
mv ffmpeg-*-amd64-static/ffprobe .

# 5. Limpar os arquivos temporários que não precisamos mais
rm -rf ffmpeg.tar.xz ffmpeg-*-amd64-static

# 6. Dar permissão de execução aos binários
chmod +x ffmpeg ffprobe

echo "🚀 FFmpeg e FFprobe instalados com sucesso na raiz!"