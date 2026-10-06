"""
Envia a pasta de um projeto NENC para a Jornada de Compra: nome antigo de
`scripts/nenc_enviar.py`.

    ..\\.venv\\Scripts\\python scripts\\jornada_enviar.py "X:\\ALS\\1234-Estudo" --simular

é o mesmo que `scripts\\nenc_enviar.py ... --modulo jornada_compra` (o módulo
padrão). Importar este módulo devolve o próprio `nenc_enviar`: os comandos,
atalhos e testes que já usavam este nome continuam valendo, inclusive o que
eles substituem nele (`CHUNK_BYTES`, `sha256_file`, `find_ffmpeg`...).
"""

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from scripts import nenc_enviar  # noqa: E402

if __name__ == "__main__":
    sys.exit(nenc_enviar.main())
else:
    sys.modules[__name__] = nenc_enviar
