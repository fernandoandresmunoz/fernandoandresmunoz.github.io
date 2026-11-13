#!/usr/bin/env bash
# Regenera libros.json + imágenes y publica en GitHub Pages. Pensado para correr con cron.
#
#   Prueba manual:   ./actualizar.sh
#   Simular cron:    env -i HOME="$HOME" PATH=/usr/bin:/bin ./actualizar.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Cron arranca con un PATH mínimo
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export GIT_SSH_COMMAND="ssh -o BatchMode=yes"   # si falta la llave, falla en vez de quedarse esperando clave
export GIT_TERMINAL_PROMPT=0

# Sin terminal (cron) todo va a cron.log; se recorta solo cuando pasa de ~1 MB
if [ ! -t 1 ]; then
  if [ -f cron.log ] && [ "$(wc -c < cron.log)" -gt 1000000 ]; then
    tail -c 200000 cron.log > cron.log.tmp && mv cron.log.tmp cron.log
  fi
  exec >> cron.log 2>&1
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="

# Evita dos ejecuciones al mismo tiempo
LOCK="${TMPDIR:-/tmp}/pagina_libros.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "Ya hay una ejecución en curso; salgo."
  exit 0
fi
trap 'rmdir "$LOCK"' EXIT

# ONESOURCE_TOKEN (y SITE_URL, opcional) salen de .env, que no se sube a git
if [ -f .env ]; then set -a; . ./.env; set +a; fi
: "${ONESOURCE_TOKEN:?Falta ONESOURCE_TOKEN: ponlo en $DIR/.env}"

# Usa el Python del venv (tiene Pillow); si no existe, el python3 del sistema
PY="$DIR/venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"

echo "Generando libros..."
"$PY" script_generator.py

# No publicar si algo salió mal y el resultado quedó casi vacío
MIN_LIBROS="${MIN_LIBROS:-50}"
if ! "$PY" -c 'import json, sys; sys.exit(0 if json.load(open("libros.json"))["total"] >= int(sys.argv[1]) else 1)' "$MIN_LIBROS"; then
  echo "libros.json tiene menos de $MIN_LIBROS libros; no publico."
  exit 1
fi

# Solo lo publicable (nunca venv ni .env). -A incluye las imágenes nuevas y las borradas.
git add -A -- *.json *.html sitemap.xml robots.txt imagenes producto detalle
if git diff --cached --quiet; then
  echo "Sin cambios; nada que publicar."
  exit 0
fi

git commit -q -m "Actualiza ofertas $(date '+%Y-%m-%d %H:%M')"
git push -q origin main
echo "Publicado."
